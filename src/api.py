"""Liquidity Radar historical read-only FastAPI application; no Helius integration."""
from contextlib import asynccontextmanager
from datetime import datetime
import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import api_contract as c
from .api_data import DatasetStore, INTERVALS, POSITIONS, SCENARIOS, Settings, finite, utc

API_VERSION = "1.0.0"
ERRORS = {400: {"model": c.ErrorResponse}, 404: {"model": c.ErrorResponse},
          422: {"model": c.ErrorResponse}, 500: {"model": c.ErrorResponse}, 503: {"model": c.ErrorResponse}}


def error_response(status, code, message, details=None):
    payload = c.ErrorResponse(error=c.ErrorInfo(code=code, message=message, details=details or []))
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"))


def checked_time(value):
    if value is None:
        return None
    try:
        return utc(value)
    except ValueError:
        raise HTTPException(422, "Dates must include a timezone, preferably UTC (Z).") from None


def create_app(settings=None, store=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        application.state.store = None
        try:
            application.state.store = store if store is not None else DatasetStore.load(settings)
        except Exception as exc:
            # Fail closed; neither response nor application log includes paths or input text.
            logging.getLogger("liquidity_radar").error("Dataset cache unavailable: %s", type(exc).__name__)
        yield
        application.state.store = None

    application = FastAPI(title="Liquidity Radar Historical MVP", version=API_VERSION,
        description="Read-only HISTORICAL data. Forward scores are not calibrated probabilities; exit cost is not observed slippage. Models are never executed by HTTP requests.",
        lifespan=lifespan, responses=ERRORS)
    allowed_queries = {"/api/health": set(), "/api/overview": {"as_of"}, "/api/market-risk": {"as_of"},
        "/api/forward-risk": {"as_of"}, "/api/positions": {"scenario", "as_of"},
        "/api/exit-cost": {"position_size", "scenario", "as_of"},
        "/api/liquidity/history": {"interval", "start", "end", "as_of", "limit", "offset"},
        "/api/backtest/summary": {"event", "sample", "horizon", "predictor"}}

    @application.middleware("http")
    async def query_contract(request, call_next):
        permitted = allowed_queries.get(request.url.path)
        if permitted is not None and request.method == "GET":
            if set(request.query_params)-permitted or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
                return error_response(422, "INVALID_PARAMETERS", "Unknown or repeated query parameters are not accepted.")
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not reflect raw query values, URLs or Pydantic input/context in responses.
        details = [c.ErrorDetail(field=".".join(map(str, item["loc"])), message="Invalid parameter") for item in exc.errors()]
        return error_response(422, "INVALID_PARAMETERS", "One or more parameters are invalid.", details)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        codes = {404: "NO_DATA", 422: "INVALID_PARAMETERS", 503: "DATASETS_UNAVAILABLE"}
        return error_response(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))

    @application.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return error_response(500, "INTERNAL_ERROR", "Unable to serve this request.")

    def cache(request):
        result = getattr(request.app.state, "store", None)
        if result is None:
            raise HTTPException(503, "Historical datasets are unavailable or invalid. Check the local dataset configuration.")
        return result

    def decision(request, as_of):
        db = cache(request)
        instant = checked_time(as_of)
        try:
            row = db.decision(instant)
        except LookupError:
            raise HTTPException(404, "No closed historical window is available at the requested instant.") from None
        return db, row, db.metadata(row.available_at, instant)

    @application.get("/api/health", response_model=c.Envelope[c.Health], tags=["Status"])
    def health(request: Request):
        db = cache(request)
        return c.Envelope[c.Health](meta=db.metadata(db.latest),
            data=c.Health(api_version=API_VERSION, datasets_ready=True, cache_loaded_at=db.loaded_at))

    @application.get("/api/overview", response_model=c.Envelope[c.Overview], tags=["Historical risk"])
    def overview(request: Request, as_of: datetime | None = Query(None, description="Only windows closed at or before this UTC instant.")):
        db, row, meta = decision(request, as_of)
        closed = db.liquidity["1m"].loc[db.liquidity["1m"].available_at.le(row.available_at)]
        return c.Envelope[c.Overview](meta=meta, data=c.Overview(
            closed_window_count_1m=len(closed), swap_count=int(closed.swap_count.sum()),
            volume_usdc=finite(closed.volume_usdc.sum(min_count=1)), net_flow_usdc=finite(closed.net_flow_usdc.sum(min_count=1)),
            empty_window_count_1m=int(closed.swap_count.eq(0).sum()), missing_volatility_count_1m=int(closed.volatility.isna().sum()),
            current_market_risk=db.market_risk(row), forward_liquidity_risk=db.forward_risk(row), positions_normal=db.positions_at(row)))

    @application.get("/api/market-risk", response_model=c.Envelope[c.MarketRisk], tags=["Historical risk"])
    def market_risk(request: Request, as_of: datetime | None = None):
        db, row, meta = decision(request, as_of)
        return c.Envelope[c.MarketRisk](meta=meta, data=db.market_risk(row))

    @application.get("/api/forward-risk", response_model=c.Envelope[c.ForwardRisk], tags=["Historical risk"])
    def forward_risk(request: Request, as_of: datetime | None = None):
        db, row, meta = decision(request, as_of)
        return c.Envelope[c.ForwardRisk](meta=meta, data=db.forward_risk(row))

    @application.get("/api/positions", response_model=c.Envelope[c.Positions], tags=["Position exit"])
    def positions(request: Request, scenario: c.Scenario = "NORMAL", as_of: datetime | None = None):
        db, row, meta = decision(request, as_of)
        return c.Envelope[c.Positions](meta=meta, data=c.Positions(position_sizes_usdc=list(POSITIONS),
            scenarios=list(SCENARIOS), selected_scenario=scenario, items=db.positions_at(row, scenario)))

    @application.get("/api/exit-cost", response_model=c.Envelope[c.PositionExit], tags=["Position exit"])
    def exit_cost(request: Request, position_size: int = Query(100000, description="USDC: 10000, 50000, 100000, 250000, 500000, 1000000"),
                  scenario: c.Scenario = "NORMAL", as_of: datetime | None = None):
        if position_size not in POSITIONS:
            raise HTTPException(422, "Unsupported position_size. Allowed USDC positions: 10000, 50000, 100000, 250000, 500000, 1000000.")
        db, row, meta = decision(request, as_of)
        item = next(item for item in db.positions_at(row, scenario) if item.position_size_usdc == position_size)
        return c.Envelope[c.PositionExit](meta=meta, data=item)

    @application.get("/api/liquidity/history", response_model=c.Envelope[c.History], tags=["Historical series"])
    def history(request: Request, interval: c.Interval = "1m", start: datetime | None = None, end: datetime | None = None,
                as_of: datetime | None = None, limit: int = Query(min(300, settings.history_max_limit), ge=1, le=settings.history_max_limit), offset: int = Query(0, ge=0)):
        if limit > settings.history_max_limit:
            raise HTTPException(422, f"limit must not exceed {settings.history_max_limit}.")
        start, end = checked_time(start), checked_time(end)
        if start is not None and end is not None and start >= end:
            raise HTTPException(422, "start must precede end; end is exclusive.")
        db, row, meta = decision(request, as_of)
        frame = db.liquidity[interval]
        mask = frame.available_at.le(row.available_at)
        if start is not None:
            mask &= frame.timestamp.ge(start)
        if end is not None:
            mask &= frame.timestamp.lt(end)
        selected = frame.loc[mask]
        total = len(selected)
        next_offset = offset+limit if offset+limit < total else None
        items = [db.liquidity_window(item).model_copy(update={"risk_at_close": db.risk_at_close(item.available_at)})
                 for _, item in selected.iloc[offset:offset+limit].iterrows()]
        return c.Envelope[c.History](meta=meta, data=c.History(interval=interval,
            pagination=c.Pagination(total=total, offset=offset, limit=limit, next_offset=next_offset), items=items))

    @application.get("/api/backtest/summary", response_model=c.Envelope[c.Backtest], tags=["Retrospective evaluation"])
    def backtest_summary(request: Request, event: c.Event = "ANY_DETERIORATION", sample: c.Sample = "MATCHED",
                         horizon: int | None = Query(None, description="5, 15, 30 or 60 minutes"),
                         predictor: c.Predictor | None = None):
        if horizon is not None and horizon not in (5, 15, 30, 60):
            raise HTTPException(422, "horizon must be 5, 15, 30 or 60 minutes.")
        db = cache(request)
        frame = db.backtest
        mask = frame.event.eq(event) & frame["sample"].eq(sample)
        if horizon is not None:
            mask &= frame.horizon_minutes.eq(horizon)
        if predictor is not None:
            mask &= frame.predictor.eq(predictor)
        selected = frame.loc[mask].sort_values(["horizon_minutes", "predictor"])
        source = db.provenance
        thresholds = source["backtest_reference"]["thresholds"]
        available = utc(source["backtest_result_available_at"])
        return c.Envelope[c.Backtest](meta=db.metadata(available), data=c.Backtest(
            train_start=utc(source["train_start"]), test_start=utc(source["test_start"]), test_end_exclusive=utc(source["test_end_exclusive"]),
            result_available_at=available, frozen_volume_p05_usdc=thresholds["volume_p05"],
            frozen_volatility_p95_std_log_return=thresholds["volatility_p95"], frozen_exit_cost_p95_pct=thresholds["exit_cost_p95"],
            items=[db.backtest_row(record) for record in selected.to_dict("records")], limitations=[
                "One previously inspected test day; not pristine prospective validation.",
                "Overlapping future horizons are dependent; non-overlapping sample is small.",
                "Long horizons saturate event prevalence; precision alone is not discrimination.",
                "Exit cost is an unvalidated volatility/turnover proxy, not observed slippage.",
                "Lead time deduplicates first-breach minutes, not independent physical episodes."]))

    # Outermost CORS also decorates validation error responses for the frontend.
    application.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                               allow_credentials=False, allow_methods=["GET"], allow_headers=["Accept", "Content-Type"])
    return application


app = create_app()
