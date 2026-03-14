# Seasons Care Services — Server Setup Guide
# Server: 74.208.68.239 (same VPS as Bill Briefer)
# Domain: seasonscareservices.com
# Container: seasons on port 8040
# ===================================================
#
# Steve — run these commands one section at a time.
# SSH into the server first:
#   ssh root@74.208.68.239
#
# Each section has a VERIFY step so you know it worked.
# ===================================================


# ===================================================
# STEP 1: Verify DNS is pointing to this server
# ===================================================

ping -c 2 seasonscareservices.com
# VERIFY: Should show 74.208.68.239. If not, DNS hasn't propagated yet — wait and retry.


# ===================================================
# STEP 2: Create the project directory
# ===================================================

mkdir -p /app/seasons/data
cd /app/seasons

# VERIFY:
pwd
# Should show: /app/seasons


# ===================================================
# STEP 3: Upload project files
# ===================================================
# From your LOCAL PowerShell (not on the server), run:
#
#   scp -r C:\path\to\seasons-setup\* root@74.208.68.239:/app/seasons/
#
# Or if you prefer, you can copy-paste the file contents
# directly on the server using nano/vim.
#
# Files to upload:
#   Dockerfile
#   docker-compose.yml
#   requirements.txt
#   app/  (entire directory)
#   nginx-seasons  (goes to /app/seasons/ temporarily)

# VERIFY: Check the files are in place
ls -la /app/seasons/
# Should show: Dockerfile, docker-compose.yml, requirements.txt, app/, data/
ls -la /app/seasons/app/
# Should show: main.py, __init__.py, database/, routes/, templates/, static/


# ===================================================
# STEP 4: Build and start the Docker container
# ===================================================

cd /app/seasons
docker-compose build
docker-compose up -d

# VERIFY: Container is running
docker ps | grep seasons
# Should show the "seasons" container running on port 8040

# Double-check with curl:
curl http://localhost:8040/health
# Should return: {"status":"ok","service":"seasons-care-services"}


# ===================================================
# STEP 5: Set up Nginx reverse proxy
# ===================================================

# Copy the nginx config to sites-available
cp /app/seasons/nginx-seasons /etc/nginx/sites-available/seasons

# Create symlink to enable it
ln -sf /etc/nginx/sites-available/seasons /etc/nginx/sites-enabled/seasons

# Test nginx config
nginx -t
# Should say: syntax is ok / test is successful

# Reload nginx
systemctl reload nginx

# VERIFY: Hit the site via HTTP
curl -I http://seasonscareservices.com
# Should return HTTP 200


# ===================================================
# STEP 6: Set up SSL with Certbot
# ===================================================

certbot --nginx -d seasonscareservices.com -d www.seasonscareservices.com

# Certbot will:
#   - Get the SSL certificate from Let's Encrypt
#   - Automatically modify the nginx config to add HTTPS
#   - Set up HTTP -> HTTPS redirect
#
# When prompted:
#   - Enter email if asked (or it may already have it from Bill Briefer)
#   - Agree to terms
#   - Choose to redirect HTTP to HTTPS (option 2)

# VERIFY: Hit the site via HTTPS
curl -I https://seasonscareservices.com
# Should return HTTP 200 with valid SSL

# Also verify in your browser:
# Open https://seasonscareservices.com
# Should show the "Server is live" page with Seasons branding
# Should show a lock icon (valid SSL)


# ===================================================
# STEP 7: Verify auto-renewal
# ===================================================

certbot renew --dry-run
# Should say: Congratulations, all simulated renewals succeeded


# ===================================================
# DONE! Server is live.
# ===================================================
#
# What's running:
#   - Docker container "seasons" on port 8040
#   - FastAPI app with SQLite database at /app/seasons/data/seasons.db
#   - Nginx proxying seasonscareservices.com -> localhost:8040
#   - HTTPS via Let's Encrypt (auto-renews)
#   - Health check at /health
#
# Useful commands:
#   docker logs seasons              — view app logs
#   docker-compose restart           — restart the app
#   docker-compose down              — stop the app
#   docker-compose up -d --build     — rebuild and restart
#   docker exec -it seasons bash     — shell into the container
#
# Next: Build the Meal Planner module (Phase 1)
# ===================================================
