

## Setup
Setup postgreSQL databases:
```bash
sudo apt update && sudo apt install -y postgresql postgresql-contrib
sudo service postgresql start
sudo -u postgres psql <<EOF
CREATE USER stockdevuser WITH PASSWORD 'stockdevpass';
CREATE USER stockbtuser WITH PASSWORD 'stockbtpass';
CREATE DATABASE stock_trader_dev OWNER stockdevuser;
CREATE DATABASE stock_trader_bt  OWNER stockbtuser;
EOF
```