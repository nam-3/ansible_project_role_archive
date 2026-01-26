"""
Archive Platform - Web Application Example
FastAPI application with DB (via DBLB) and Monitoring integration

Dependencies:
    pip install fastapi uvicorn sqlalchemy psycopg2-binary prometheus-client
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
import logging
import os

# ========================================
# Configuration
# ========================================

# Database Configuration (DBLB VIP)
DB_HOST = os.getenv("DB_HOST", "192.168.20.100")  # DBLB VIP
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASS = os.getenv("DB_PASS", "Soldesk1.")
DB_NAME = os.getenv("DB_NAME", "cmp_db")

SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Monitoring Configuration
MONITORING_ENABLED = os.getenv("MONITORING_ENABLED", "true").lower() == "true"
MONITORING_HOST = os.getenv("MONITORING_HOST", "172.16.6.127")  # Monitoring Server

# ========================================
# Database Setup
# ========================================

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Check connection health before using
    connect_args={"connect_timeout": 5}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ========================================
# Database Models
# ========================================

class AccessLog(Base):
    """Example model for tracking access logs"""
    __tablename__ = "access_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String, index=True)
    method = Column(String)
    status_code = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)
    client_ip = Column(String)

# Create tables
try:
    Base.metadata.create_all(bind=engine)
    logging.info("Database tables created successfully")
except Exception as e:
    logging.error(f"Failed to create tables: {e}")

# ========================================
# Prometheus Metrics
# ========================================

# Counters
http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

db_queries_total = Counter(
    'db_queries_total',
    'Total database queries',
    ['operation', 'status']
)

# Histograms
http_request_duration = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint']
)

db_query_duration = Histogram(
    'db_query_duration_seconds',
    'Database query duration in seconds',
    ['operation']
)

# ========================================
# FastAPI Application
# ========================================

app = FastAPI(
    title="Archive Platform Web API",
    description="Web application with DBLB and Monitoring integration",
    version="1.0.0"
)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ========================================
# Routes
# ========================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Homepage with system information"""
    hostname = os.getenv("HOSTNAME", "unknown")
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Archive Platform</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
            .container {{ background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ color: #2563eb; }}
            .info {{ background: #eff6ff; padding: 15px; border-left: 4px solid #2563eb; margin: 10px 0; }}
            .status {{ display: inline-block; padding: 5px 10px; border-radius: 4px; font-weight: bold; }}
            .status.ok {{ background: #10b981; color: white; }}
            a {{ color: #2563eb; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Archive Platform Web Server</h1>
            <div class="info">
                <strong>Hostname:</strong> {hostname}<br>
                <strong>Database:</strong> {DB_HOST}:{DB_PORT} <span class="status ok">Connected</span><br>
                <strong>Monitoring:</strong> {MONITORING_HOST} <span class="status ok">Active</span>
            </div>
            <h2>Available Endpoints</h2>
            <ul>
                <li><a href="/health">/health</a> - Health check</li>
                <li><a href="/db/status">/db/status</a> - Database status</li>
                <li><a href="/db/test">/db/test</a> - Database write/read test</li>
                <li><a href="/metrics">/metrics</a> - Prometheus metrics</li>
                <li><a href="/docs">/docs</a> - API documentation</li>
            </ul>
        </div>
    </body>
    </html>
    """
    
    # Record metrics
    http_requests_total.labels(method='GET', endpoint='/', status='200').inc()
    
    return html_content

@app.get("/health")
async def health_check():
    """Health check endpoint for load balancer"""
    try:
        # Test DB connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        http_requests_total.labels(method='GET', endpoint='/health', status='200').inc()
        
        return {
            "status": "healthy",
            "database": "connected",
            "monitoring": "active" if MONITORING_ENABLED else "disabled",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        http_requests_total.labels(method='GET', endpoint='/health', status='503').inc()
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")

@app.get("/db/status")
async def database_status():
    """Get database connection status and info"""
    try:
        with db_query_duration.labels(operation='status').time():
            with engine.connect() as conn:
                # Get PostgreSQL version
                result = conn.execute(text("SELECT version()"))
                version = result.fetchone()[0]
                
                # Get current database
                result = conn.execute(text("SELECT current_database()"))
                current_db = result.fetchone()[0]
                
                # Get connection count
                result = conn.execute(text("SELECT count(*) FROM pg_stat_activity"))
                connections = result.fetchone()[0]
        
        db_queries_total.labels(operation='status', status='success').inc()
        http_requests_total.labels(method='GET', endpoint='/db/status', status='200').inc()
        
        return {
            "status": "connected",
            "host": DB_HOST,
            "port": DB_PORT,
            "database": current_db,
            "version": version,
            "active_connections": connections,
            "pool_size": engine.pool.size(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        db_queries_total.labels(operation='status', status='error').inc()
        http_requests_total.labels(method='GET', endpoint='/db/status', status='500').inc()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/db/test")
async def database_test(db: Session = Depends(get_db)):
    """Test database write and read operations"""
    try:
        with db_query_duration.labels(operation='write').time():
            # Write test
            log_entry = AccessLog(
                endpoint="/db/test",
                method="GET",
                status_code=200,
                client_ip="test",
                timestamp=datetime.utcnow()
            )
            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)
        
        with db_query_duration.labels(operation='read').time():
            # Read test
            recent_logs = db.query(AccessLog).order_by(
                AccessLog.timestamp.desc()
            ).limit(5).all()
        
        db_queries_total.labels(operation='write', status='success').inc()
        db_queries_total.labels(operation='read', status='success').inc()
        http_requests_total.labels(method='GET', endpoint='/db/test', status='200').inc()
        
        return {
            "status": "success",
            "write": {
                "id": log_entry.id,
                "timestamp": log_entry.timestamp.isoformat()
            },
            "read": {
                "count": len(recent_logs),
                "latest": [
                    {
                        "id": log.id,
                        "endpoint": log.endpoint,
                        "timestamp": log.timestamp.isoformat()
                    } for log in recent_logs
                ]
            }
        }
    except Exception as e:
        db_queries_total.labels(operation='write', status='error').inc()
        http_requests_total.labels(method='GET', endpoint='/db/test', status='500').inc()
        raise HTTPException(status_code=500, detail=f"Database test failed: {str(e)}")

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint for monitoring server"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/info")
async def system_info():
    """System configuration information"""
    return {
        "application": "Archive Platform Web API",
        "version": "1.0.0",
        "database": {
            "host": DB_HOST,
            "port": DB_PORT,
            "name": DB_NAME,
            "type": "PostgreSQL (via DBLB)"
        },
        "monitoring": {
            "enabled": MONITORING_ENABLED,
            "host": MONITORING_HOST,
            "metrics_endpoint": "/metrics"
        },
        "hostname": os.getenv("HOSTNAME", "unknown"),
        "timestamp": datetime.utcnow().isoformat()
    }

# ========================================
# Startup Event
# ========================================

@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    logging.basicConfig(level=logging.INFO)
    logging.info("=" * 50)
    logging.info("Archive Platform Web API Starting...")
    logging.info(f"Database: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    logging.info(f"Monitoring: {MONITORING_HOST} ({'Enabled' if MONITORING_ENABLED else 'Disabled'})")
    logging.info("=" * 50)
    
    # Test database connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logging.info("✓ Database connection successful")
    except Exception as e:
        logging.error(f"✗ Database connection failed: {e}")

# ========================================
# Run Application
# ========================================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
