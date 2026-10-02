# CompanyAgent production deployment (today)

This deployment runs the only backend supplied in this repository: the Python/FastAPI service. The Vue frontend reaches it through `/api/python`; the UI's Java option must remain unused until a real Java service is added.

## Before uploading code

1. Create an Ubuntu 24.04 VM with at least 4 GB RAM, 2 vCPUs and 40 GB disk. Add your SSH public key during creation.
2. Create an `A` DNS record for a subdomain such as `companyagent.yourdomain.com`, pointing to the VM's public IPv4 address. Wait until `nslookup companyagent.yourdomain.com` returns that address.
3. Allow inbound TCP 22, 80 and 443 in the cloud firewall. Do not open 6379, 8000, 8001 or 9090.
4. Make sure `CompanyAgent/.env` contains a working `ANTHROPIC_API_KEY`, an appropriate `ANTHROPIC_MODEL` (and `ANTHROPIC_BASE_URL` if applicable), and a strong random `REDIS_PASSWORD`. Use the **same** `REDIS_PASSWORD` in `deploy/.env.production`; Docker Compose needs it before it starts containers. Never commit either secret file.

## First deployment on the VM

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit
```

Reconnect, then clone or upload this entire `CompanyAgent所有代码+简历` directory. From its root:

```bash
cp deploy/.env.production.example deploy/.env.production
nano deploy/.env.production       # replace DOMAIN and REDIS_PASSWORD
nano CompanyAgent/.env                # set secrets; do not copy it to GitHub
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml ps
curl -fsS https://YOUR_DOMAIN/api/python/health
```

The first build can take several minutes because the backend image downloads the Chroma embedding model. Caddy automatically obtains and renews HTTPS after DNS and ports 80/443 are correct.

## End-to-end acceptance

Open `https://YOUR_DOMAIN`, leave the backend selector on **Python**, click the health check, then send one chat message. Also verify:

```bash
curl -fsS https://YOUR_DOMAIN/api/python/knowledge/stats
curl -fsS https://YOUR_DOMAIN/api/python/skills
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml logs --tail=100 companyagent
```

## Updating and recovery

```bash
git pull
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml ps
```

Data is retained in named Docker volumes (`redis_data`, `chromadb_data`, and `companyagent_eval`). Do not run `docker compose down -v` in production. Back up those volumes before changing storage or deleting the VM.
