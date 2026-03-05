# NeoDemos Deployment Guide

**Version**: 1.0  
**Date**: March 1, 2026  
**Status**: Production Ready

---

## Quick Start

### Development Environment (Local)

```bash
# 1. Clone/setup repository
cd NeoDemos

# 2. Install dependencies
pip install -r requirements.txt

# 3. Setup environment
cp .env.example .env
# Edit .env with your database credentials and API keys

# 4. Initialize database
python scripts/init_db.py

# 5. Ingest initial city data (default: Rotterdam)
python scripts/ingest_data.py

# 6. Run development server
python main.py
# Server runs on http://localhost:8000
```

### Production Environment (Recommended)

See **Docker Deployment** section below.

---

## Prerequisites

### System Requirements
- **Python**: 3.10+ (tested on 3.13.3)
- **PostgreSQL**: 16.13+ (or 15+)
- **RAM**: Minimum 2GB, recommended 4GB
- **Disk**: Minimum 5GB for data, 10GB recommended
- **Network**: Access to OpenRaadsinformatie API

### API Keys Required

1. **Google Gemini API Key** (for LLM analysis)
   - Get from: https://makersuite.google.com/app/apikey
   - Free tier available (60 requests/min)
   - Environment variable: `GEMINI_API_KEY`

2. **OpenRaadsinformatie API** (optional)
   - Public API: no key required
   - Better rate limits with API key
   - Environment variable: `ORI_API_KEY` (optional)

---

## Environment Configuration

### Create `.env` file in project root

```env
# ==================== CITY CONFIGURATION ====================
# Which city does this deployment serve?
# Options: rotterdam, amsterdam, den_haag, groningen, etc.
NEODEMOS_CITY=rotterdam

# ==================== DATABASE ====================
DB_HOST=localhost
DB_PORT=5432
DB_NAME=neodemos
DB_USER=postgres
DB_PASSWORD=your_secure_password_here
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# ==================== API KEYS ====================
# Required: Google Gemini API for LLM analysis
GEMINI_API_KEY=your_gemini_api_key_here

# Optional: OpenRaadsinformatie API key for better rate limits
ORI_API_KEY=optional_key_here

# ==================== SERVER CONFIGURATION ====================
HOST=0.0.0.0
PORT=8000
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO

# ==================== SECURITY ====================
SECRET_KEY=generate_with_secrets.token_urlsafe(32)
ALLOWED_HOSTS=localhost,127.0.0.1,your_domain.com

# ==================== OPTIONAL ====================
# Email for error notifications
ADMIN_EMAIL=admin@example.com

# Number of workers for background tasks
WORKER_PROCESSES=4

# Daily refresh time (24-hour format, UTC)
DAILY_REFRESH_HOUR=8
DAILY_REFRESH_MINUTE=0
```

### Generate Secure Secret Key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Database Setup

### PostgreSQL Installation

**macOS (Homebrew)**:
```bash
brew install postgresql@16
brew services start postgresql@16
```

**Linux (Ubuntu/Debian)**:
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
```

**Docker**:
```bash
docker run -d \
  --name neodemos-postgres \
  -e POSTGRES_DB=neodemos \
  -e POSTGRES_PASSWORD=your_password \
  -p 5432:5432 \
  postgres:16
```

### Initialize Database

```bash
# Create database and schema
python scripts/init_db.py

# Verify tables created
psql -h localhost -U postgres -d neodemos -c "\dt"
# Should show: meetings, agenda_items, documents, party_profiles, party_statements, etc.
```

### Database Backup & Restore

**Backup**:
```bash
pg_dump -h localhost -U postgres -d neodemos > neodemos_backup_$(date +%Y%m%d).sql
```

**Restore**:
```bash
psql -h localhost -U postgres -d neodemos < neodemos_backup_20260301.sql
```

---

## Local Development

### Install Dependencies

```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate  # Windows

pip install -r requirements.txt
```

### Run Development Server

```bash
python main.py
```

Server starts on `http://localhost:8000`

**Features**:
- Auto-reload on code changes
- Detailed error messages
- APScheduler runs in background thread
- Daily refresh at 8 AM UTC

### Test Suite

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test
python -m pytest tests/test_party_lens_e2e.py -v

# Run tests for specific city
NEODEMOS_CITY=rotterdam python -m pytest tests/ -v

# Generate coverage report
python -m pytest tests/ --cov=services --cov-report=html
```

### Data Ingestion (Local)

```bash
# Fetch and ingest Rotterdam data
python scripts/ingest_data.py

# Fetch data for different city
NEODEMOS_CITY=amsterdam python scripts/ingest_data.py

# Just fetch notulen (don't analyze)
python scripts/fetch_notulen.py

# Fix data quality issues
python scripts/fix_notulen_data.py --city rotterdam

# Extract party positions from notulen
NEODEMOS_CITY=rotterdam python scripts/extract_party_positions.py
```

---

## Docker Deployment

### Dockerfile

Create `Dockerfile` in project root:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose (Complete Stack)

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  # PostgreSQL Database
  postgres:
    image: postgres:16
    container_name: neodemos-postgres
    environment:
      POSTGRES_DB: neodemos
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init_db.py:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - neodemos-network

  # FastAPI Application
  web:
    build: .
    container_name: neodemos-web
    environment:
      - NEODEMOS_CITY=${NEODEMOS_CITY}
      - DB_HOST=postgres
      - DB_PORT=5432
      - DB_NAME=neodemos
      - DB_USER=postgres
      - DB_PASSWORD=${DB_PASSWORD}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - ENVIRONMENT=production
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./data:/app/data
      - ./output:/app/output
      - ./logs:/app/logs
    networks:
      - neodemos-network
    restart: unless-stopped

  # Optional: Nginx Reverse Proxy
  nginx:
    image: nginx:alpine
    container_name: neodemos-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - web
    networks:
      - neodemos-network
    restart: unless-stopped

volumes:
  postgres_data:

networks:
  neodemos-network:
    driver: bridge
```

### Build and Run

```bash
# Build Docker images
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f web

# Stop services
docker-compose down

# Stop and remove volumes (WARNING: deletes database!)
docker-compose down -v
```

### Access Application

- **Web UI**: http://localhost
- **API**: http://localhost:8000
- **Database**: localhost:5432

---

## Production Deployment (Linux Server)

### Server Setup

**Recommended**: Ubuntu 22.04 LTS on AWS EC2, DigitalOcean, Linode, or Hetzner

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker & Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Verify installation
docker --version
docker-compose --version
```

### Deploy Application

```bash
# Clone repository
git clone https://github.com/yourusername/neodemos.git
cd neodemos

# Create production .env file
nano .env
# Add all required variables (see Environment Configuration above)

# Create SSL certificates (optional but recommended)
sudo apt install certbot python3-certbot-nginx
sudo certbot certonly --standalone -d yourdomain.com

# Build and start services
docker-compose -f docker-compose.yml up -d

# Verify services are running
docker-compose ps
# Should show: neodemos-postgres, neodemos-web, neodemos-nginx all running

# Check logs for errors
docker-compose logs -f web
```

### SSL/HTTPS Setup (Nginx)

Create `nginx.conf`:

```nginx
upstream web {
    server web:8000;
}

server {
    listen 80;
    server_name yourdomain.com;
    
    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;
    
    # SSL certificates from certbot
    ssl_certificate /etc/nginx/certs/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/yourdomain.com/privkey.pem;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    
    # Proxy requests to FastAPI
    location / {
        proxy_pass http://web;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Static files
    location /static {
        alias /app/static;
        expires 1d;
    }
}
```

### Monitoring & Maintenance

```bash
# Check disk usage
docker exec neodemos-postgres du -sh /var/lib/postgresql/data

# Database backup (automated daily)
cat > /usr/local/bin/backup-neodemos.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/backups/neodemos"
DATE=$(date +%Y%m%d_%H%M%S)
docker exec neodemos-postgres pg_dump -U postgres neodemos | gzip > $BACKUP_DIR/neodemos_$DATE.sql.gz
# Keep only last 7 days
find $BACKUP_DIR -mtime +7 -delete
EOF

chmod +x /usr/local/bin/backup-neodemos.sh

# Add to crontab (runs daily at 2 AM)
(crontab -l 2>/dev/null; echo "0 2 * * * /usr/local/bin/backup-neodemos.sh") | crontab -

# Monitor application
docker stats neodemos-web neodemos-postgres

# View recent logs
docker-compose logs -f --tail=100 web
```

---

## Multi-City Deployment

### Scenario 1: Single Server, All Cities

Deploy same container with different environments:

```bash
# Start Rotterdam
docker-compose -f docker-compose.yml -e NEODEMOS_CITY=rotterdam up -d web-rotterdam

# Start Amsterdam
docker-compose -f docker-compose.yml -e NEODEMOS_CITY=amsterdam up -d web-amsterdam

# They share same PostgreSQL database
# Data isolated by city in database
```

### Scenario 2: Separate Servers per City

Deploy each city on different server with dedicated infrastructure:

```bash
# Server 1: rotterdam.example.com
NEODEMOS_CITY=rotterdam docker-compose up -d

# Server 2: amsterdam.example.com
NEODEMOS_CITY=amsterdam docker-compose up -d

# Server 3: den-haag.example.com
NEODEMOS_CITY=den_haag docker-compose up -d

# Each has independent PostgreSQL instance
```

### Scenario 3: Kubernetes Cluster

Deploy using Helm charts (for large scale):

```bash
# Create namespace per city
kubectl create namespace rotterdam
kubectl create namespace amsterdam

# Deploy NeoDemos Helm chart
helm install neodemos-rotterdam ./helm \
  --namespace rotterdam \
  --set city=rotterdam

helm install neodemos-amsterdam ./helm \
  --namespace amsterdam \
  --set city=amsterdam

# Each pod auto-scales independently
```

---

## Troubleshooting

### Common Issues

**1. Database Connection Failed**
```
Error: could not connect to server
```

**Solution**:
```bash
# Check PostgreSQL is running
docker-compose ps
# Should show postgres service as "Up"

# Check database credentials in .env
cat .env | grep DB_

# Test connection
docker exec neodemos-postgres psql -U postgres -c "SELECT version();"
```

**2. Gemini API Key Invalid**
```
Error: API key not valid
```

**Solution**:
```bash
# Verify key in .env
cat .env | grep GEMINI_API_KEY

# Test API connectivity
python -c "
import google.generativeai as genai
genai.configure(api_key='YOUR_KEY')
print(genai.list_models())
"
```

**3. Out of Memory**
```
Error: Container killed due to memory limit
```

**Solution**:
```bash
# Increase Docker memory limit in docker-compose.yml
services:
  web:
    ...
    deploy:
      resources:
        limits:
          memory: 4G
        reservations:
          memory: 2G

# Rebuild and restart
docker-compose down
docker-compose up -d
```

**4. OpenRaadsinformatie API Rate Limited**
```
Error: Too many requests (429)
```

**Solution**:
```bash
# Add API key to .env for better rate limits
ORI_API_KEY=your_key_here

# Or implement exponential backoff in fetch_notulen.py
# (already implemented - adjust delays if needed)
```

### Debug Logging

```bash
# Enable debug logging
LOG_LEVEL=DEBUG python main.py

# Or in docker-compose.yml
environment:
  - LOG_LEVEL=DEBUG

# View detailed logs
docker-compose logs -f --tail=200 web
```

---

## Scaling Considerations

### Vertical Scaling (Bigger Server)

- Increase RAM: 2GB → 4GB → 8GB
- Use faster SSD storage
- Upgrade CPU cores
- Increase PostgreSQL `shared_buffers`

### Horizontal Scaling (Multiple Servers)

1. **Load Balancer** (AWS ELB, Nginx, HAProxy)
   - Routes requests across multiple web servers
   - Health checks ensure only healthy servers receive traffic

2. **Database Replication**
   - PostgreSQL primary-replica setup
   - Replicas handle read operations
   - Primary handles writes

3. **Cache Layer** (Redis)
   - Cache party profile queries
   - Cache LLM analysis results
   - Reduces database and API load

### Performance Tuning

```python
# In .env
DB_POOL_SIZE=20           # Connection pooling
DB_MAX_OVERFLOW=40        # Overflow connections
WORKER_PROCESSES=4        # Background workers
CACHE_TTL=3600           # Cache party profiles for 1 hour
```

---

## Security Best Practices

### 1. API Key Management
```bash
# Store in environment variables, never commit to git
.env                    # ← Add to .gitignore
.gitignore:
    .env
    .env.local
    *.pem
    *.key
```

### 2. Database Security
```bash
# Use strong password
DB_PASSWORD=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Restrict PostgreSQL access
# In pg_hba.conf: only allow localhost/internal network
host    all             all             127.0.0.1/32            md5
host    all             all             10.0.0.0/8              md5

# Enable SSL connections
ssl = on
```

### 3. Web Server Security
```nginx
# In nginx.conf
# Disable server version
server_tokens off;

# Rate limiting
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
limit_req zone=api burst=20 nodelay;

# CORS headers
add_header Access-Control-Allow-Origin "https://trusted-domain.com";
```

### 4. Regular Updates
```bash
# Weekly security updates
docker-compose pull
docker-compose up -d

# Monitor for CVEs in dependencies
pip install safety
safety check
```

---

## Monitoring & Alerts

### Health Checks

Application has built-in health endpoint:

```bash
curl http://localhost:8000/health
# Returns: {"status": "ok", "database": "connected"}
```

### Metrics to Monitor

```
- CPU usage: < 60% idle
- Memory: < 80% utilized
- Disk: < 80% filled
- Database connections: < 20 active
- Response time: < 500ms p95
- Error rate: < 0.1%
```

### Log Aggregation (Optional)

```bash
# Send logs to ELK Stack / Datadog / CloudWatch
docker-compose logs web | \
  curl -X POST -d @- https://your-logging-service/api/logs
```

---

## Backup & Disaster Recovery

### Automated Backups

```bash
# Daily backup script (added to cron)
docker exec neodemos-postgres \
  pg_dump -U postgres neodemos | \
  gzip > /backups/neodemos_$(date +%Y%m%d).sql.gz

# Store in cloud (S3, GCP, Azure)
aws s3 cp /backups/neodemos_*.sql.gz s3://my-backups/
```

### Restore from Backup

```bash
# Stop application
docker-compose down

# Restore database
zcat /backups/neodemos_20260301.sql.gz | \
  docker exec -i neodemos-postgres psql -U postgres

# Restart
docker-compose up -d
```

### RTO/RPO Targets

- **Recovery Time Objective (RTO)**: < 15 minutes (restore from backup)
- **Recovery Point Objective (RPO)**: < 1 day (daily backups)

---

## Support & Documentation

- **API Docs**: http://your-domain.com/docs (Swagger UI)
- **ReDoc**: http://your-domain.com/redoc
- **GitHub**: https://github.com/yourusername/neodemos
- **Issues**: Report bugs at GitHub Issues

---

**Last Updated**: March 1, 2026  
**Maintained By**: NeoDemos Development Team
