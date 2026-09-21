from pathlib import Path
from typing import List, Optional
import sqlite3

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "magaya_analytics.db"
INTERNAL_CARRIERS = ("JDL Airbus 700", "Air JDL")
EXCLUDED_STATUSES = ("Empty", "Pending")

app = FastAPI(title="Magaya Analytics API")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    index_path = BASE_DIR / "static" / "index.html"
    return index_path.read_text(encoding="utf-8")


@app.get("/static/airlines.html", response_class=HTMLResponse)
def airlines_page() -> str:
    page_path = BASE_DIR / "static" / "airlines.html"
    return page_path.read_text(encoding="utf-8")


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def build_date_filters(start_date: Optional[str], end_date: Optional[str]) -> tuple[str, list[str]]:
    where_clauses: list[str] = []
    params: list[str] = []

    if start_date:
        where_clauses.append("date(date) >= date(?)")
        params.append(start_date)
    if end_date:
        where_clauses.append("date(date) <= date(?)")
        params.append(end_date)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    return where_sql, params


def build_shipments_filters(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_internal: bool = False,
    carrier_group: Optional[str] = None,
    exclude_status_placeholders: bool = False,
) -> tuple[str, list[str]]:
    where_clauses: list[str] = []
    params: list[str] = []

    if start_date:
        where_clauses.append("date(date) >= date(?)")
        params.append(start_date)
    if end_date:
        where_clauses.append("date(date) <= date(?)")
        params.append(end_date)

    if not include_internal:
        where_clauses.append("carrier IS NOT NULL")
        where_clauses.append("carrier <> ''")
        where_clauses.append("carrier NOT IN (?, ?)")
        params.extend(INTERNAL_CARRIERS)

    if carrier_group:
        where_clauses.append("carrier_group = ?")
        params.append(carrier_group)

    if exclude_status_placeholders:
        where_clauses.append("status IS NOT NULL")
        where_clauses.append("status <> ''")
        where_clauses.append("status NOT IN (?, ?)")
        params.extend(EXCLUDED_STATUSES)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    return where_sql, params


class ShipmentSummaryByConsignee(BaseModel):
    consignee: str
    shipment_count: int
    total_weight: float
    total_pieces: int


class ShipmentVolumeByMonth(BaseModel):
    year: int
    month: int
    shipment_count: int
    total_weight: float
    total_pieces: int


class AirlineWeightSummary(BaseModel):
    carrier: str
    total_weight: float
    shipment_count: int


class StatusSummary(BaseModel):
    status: str
    shipment_count: int
    percentage: float


class AirlineWeightOverTime(BaseModel):
    year: int
    month: int
    carrier: str
    total_weight: float


class ShipmentSearchResult(BaseModel):
    status: Optional[str]
    number: Optional[str]
    date: Optional[str]
    consignee: Optional[str]
    carrier: Optional[str]
    weight: Optional[float]
    pieces: Optional[float]


@app.get("/api/summary/by-consignee", response_model=List[ShipmentSummaryByConsignee])
def summary_by_consignee(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(20, ge=1, le=100),
):
    conn = get_db_connection()
    cur = conn.cursor()

    where_sql, params = build_date_filters(start_date, end_date)
    sql = f"""
    SELECT
        COALESCE(NULLIF(consignee, ''), 'Unknown') AS consignee,
        COUNT(*) AS shipment_count,
        COALESCE(SUM(weight), 0) AS total_weight,
        COALESCE(SUM(pieces), 0) AS total_pieces
    FROM shipments
    {where_sql}
    GROUP BY COALESCE(NULLIF(consignee, ''), 'Unknown')
    ORDER BY shipment_count DESC, total_weight DESC
    LIMIT ?
    """

    cur.execute(sql, params + [limit])
    rows = cur.fetchall()
    conn.close()

    return [
        ShipmentSummaryByConsignee(
            consignee=row["consignee"],
            shipment_count=row["shipment_count"] or 0,
            total_weight=row["total_weight"] or 0.0,
            total_pieces=row["total_pieces"] or 0,
        )
        for row in rows
    ]


@app.get("/api/summary/volume-by-month", response_model=List[ShipmentVolumeByMonth])
def volume_by_month(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    conn = get_db_connection()
    cur = conn.cursor()

    where_sql, params = build_date_filters(start_date, end_date)
    sql = f"""
    SELECT
        date_year AS year,
        date_month AS month,
        COUNT(*) AS shipment_count,
        COALESCE(SUM(weight), 0) AS total_weight,
        COALESCE(SUM(pieces), 0) AS total_pieces
    FROM shipments
    {where_sql}
    GROUP BY date_year, date_month
    HAVING date_year IS NOT NULL AND date_month IS NOT NULL
    ORDER BY date_year, date_month
    """

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    return [
        ShipmentVolumeByMonth(
            year=row["year"],
            month=row["month"],
            shipment_count=row["shipment_count"] or 0,
            total_weight=row["total_weight"] or 0.0,
            total_pieces=row["total_pieces"] or 0,
        )
        for row in rows
    ]


def fetch_airline_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    carrier_group: Optional[str] = None,
) -> List[AirlineWeightSummary]:
    conn = get_db_connection()
    cur = conn.cursor()

    where_sql, params = build_shipments_filters(
        start_date=start_date,
        end_date=end_date,
        include_internal=False,
        carrier_group=carrier_group,
    )

    sql = f"""
    SELECT
        carrier,
        COALESCE(SUM(weight), 0) AS total_weight,
        COUNT(*) AS shipment_count
    FROM shipments
    {where_sql}
    GROUP BY carrier
    ORDER BY total_weight DESC, shipment_count DESC
    """

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    return [
        AirlineWeightSummary(
            carrier=row["carrier"],
            total_weight=row["total_weight"] or 0.0,
            shipment_count=row["shipment_count"] or 0,
        )
        for row in rows
    ]


@app.get("/api/airlines/weight-summary", response_model=List[AirlineWeightSummary])
def airlines_weight_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    return fetch_airline_summary(start_date=start_date, end_date=end_date)


@app.get("/api/commercial-airlines/weight-summary", response_model=List[AirlineWeightSummary])
def commercial_airlines_weight_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    return fetch_airline_summary(start_date=start_date, end_date=end_date, carrier_group="Commercial")


@app.get("/api/conquest-airlines/weight-summary", response_model=List[AirlineWeightSummary])
def conquest_airlines_weight_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    return fetch_airline_summary(start_date=start_date, end_date=end_date, carrier_group="Conquest")


@app.get("/api/ibc-airlines/weight-summary", response_model=List[AirlineWeightSummary])
def ibc_airlines_weight_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    return fetch_airline_summary(start_date=start_date, end_date=end_date, carrier_group="IBC")


@app.get("/api/floridaair-airlines/weight-summary", response_model=List[AirlineWeightSummary])
def floridaair_airlines_weight_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    return fetch_airline_summary(start_date=start_date, end_date=end_date, carrier_group="Florida Air")


@app.get("/api/status/summary", response_model=List[StatusSummary])
def status_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    conn = get_db_connection()
    cur = conn.cursor()

    where_sql, params = build_shipments_filters(
        start_date=start_date,
        end_date=end_date,
        include_internal=True,
        exclude_status_placeholders=True,
    )

    sql = f"""
    SELECT
        status,
        COUNT(*) AS shipment_count
    FROM shipments
    {where_sql}
    GROUP BY status
    ORDER BY shipment_count DESC
    """

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    total = sum((row["shipment_count"] or 0) for row in rows) or 1
    return [
        StatusSummary(
            status=row["status"],
            shipment_count=row["shipment_count"] or 0,
            percentage=((row["shipment_count"] or 0) / total) * 100.0,
        )
        for row in rows
    ]


@app.get("/api/airlines/weight-over-time", response_model=List[AirlineWeightOverTime])
def airlines_weight_over_time(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    conn = get_db_connection()
    cur = conn.cursor()

    where_sql, params = build_shipments_filters(
        start_date=start_date,
        end_date=end_date,
        include_internal=False,
    )

    sql = f"""
    SELECT
        date_year AS year,
        date_month AS month,
        carrier,
        COALESCE(SUM(weight), 0) AS total_weight
    FROM shipments
    {where_sql}
    GROUP BY date_year, date_month, carrier
    HAVING date_year IS NOT NULL AND date_month IS NOT NULL
    ORDER BY date_year, date_month, carrier
    """

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    return [
        AirlineWeightOverTime(
            year=row["year"],
            month=row["month"],
            carrier=row["carrier"],
            total_weight=row["total_weight"] or 0.0,
        )
        for row in rows
    ]


@app.get("/api/search/shipments", response_model=List[ShipmentSearchResult])
def search_shipments(
    q: str = Query(..., min_length=1, description="Search text"),
    limit: int = Query(50, ge=1, le=500),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    conn = get_db_connection()
    cur = conn.cursor()

    where_sql, params = build_date_filters(start_date, end_date)
    like = f"%{q}%"

    if where_sql:
        where_sql = where_sql + " AND (consignee LIKE ? OR carrier LIKE ? OR number LIKE ? OR status LIKE ?)"
    else:
        where_sql = "WHERE (consignee LIKE ? OR carrier LIKE ? OR number LIKE ? OR status LIKE ?)"

    params.extend([like, like, like, like, limit])

    sql = f"""
    SELECT
        status,
        number,
        date,
        consignee,
        carrier,
        weight,
        pieces
    FROM shipments
    {where_sql}
    ORDER BY date DESC, number DESC
    LIMIT ?
    """

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    return [
        ShipmentSearchResult(
            status=row["status"],
            number=row["number"],
            date=row["date"],
            consignee=row["consignee"],
            carrier=row["carrier"],
            weight=row["weight"],
            pieces=row["pieces"],
        )
        for row in rows
    ]
