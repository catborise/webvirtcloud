[![Gitpod ready-to-code](https://img.shields.io/badge/Gitpod-ready--to--code-blue?logo=gitpod)](https://gitpod.io/#https://github.com/retspen/webvirtcloud)

# WebVirtCloud
###### Python >=3.10 & Django 5.2 LTS (tested on Python 3.10 – 3.13)

WebVirtCloud is a **web visualization and management layer** for **libvirt**-based virtualization infrastructure.

> **libvirt is the single source of truth.** WebVirtCloud reads, displays and triggers actions on libvirt state, but it is never the primary orchestrator.

- An administrator can connect to libvirt directly with **`virt-manager`** or **`virsh`** and create, delete, rename or reconfigure virtual machines. WebVirtCloud detects these changes and updates its database accordingly.
- Virtual machines keep running **even when WebVirtCloud is stopped**; the application only reflects the current state of libvirt.
- WebVirtCloud is not a full automation platform like Proxmox. Its goal is to expose libvirt's functionality through the browser in an accessible, role-based and secure way.

```
┌─────────────────────────────────────────────────────┐
│               Administrator / User                  │
└────────────┬───────────────────┬────────────────────┘
             │ Web browser        │ virt-manager / virsh
             ▼                   ▼
┌────────────────────┐  ┌─────────────────────────────┐
│   WebVirtCloud     │  │   libvirt API (direct)      │
│   (web UI layer)   │◄─┤   — separate, independent   │
└────────┬───────────┘  └──────────────┬──────────────┘
         │ libvirt API                  │
         ▼                             ▼
┌──────────────────────────────────────────────────────┐
│               libvirtd (hypervisor daemon)           │
│                    QEMU / KVM                        │
└──────────────────────────────────────────────────────┘
```

WebVirtCloud and `virt-manager`/`virsh` can manage the same libvirt host **concurrently and independently**. WebVirtCloud picks up external changes the next time the instance list page is loaded.

## Features

* QEMU/KVM hypervisor management (multiple compute nodes)
* Virtual machine lifecycle: create, delete, power management, clone, migrate
* **External change detection:** VMs created, deleted or renamed via `virt-manager` or `virsh` are reconciled into the database automatically
* Real-time hypervisor and VM statistics (CPU, RAM, disk, network)
* Storage pool and volume management
* Network and interface management
* Browser-based VNC console (noVNC)
* Role-based access control: users only see the VMs assigned to them
* 2FA (OTP / TOTP) and failed-login lockout
* SSH public key and root password management (via guestfs)
* Cloud-init datasource interface (OpenStack metadata compatible)
* REST API — OpenAPI 3.0 (Swagger & ReDoc)
* LDAP / Active Directory integration (optional)

## Contents

1. [Installation](#installation): [installer](#installer), [Docker](#docker), [manual](#manual-installation), [first login](#first-login)
2. [Adding a Compute Node](#adding-a-compute-node)
3. [Configuration](#configuration): [environment variables](#environment-variables), [HTTPS and reverse proxy](#https-and-reverse-proxy), [cloud-init datasource](#cloud-init-datasource), [LDAP](#ldap)
4. [Updating](#updating)
5. [Usage](#usage): [users and permissions](#users-roles-and-permissions), [REST API](#rest-api), [screenshots](#screenshots)
6. [Development](#development)

## Installation

The installer supports Ubuntu 22.04 / 24.04, Debian 12, RHEL / Rocky Linux / AlmaLinux / Oracle Linux 10, openSUSE Leap 15.x / Tumbleweed and SLES 15; the panel runs on a virtual machine, a physical host or a KVM host itself and needs Python 3.10 or newer. CI tests Python 3.10–3.13 on Ubuntu (sqlite, and PostgreSQL on 3.12) and the Docker image; the distro installs are not tested in CI. Pick one of the three ways below, then do the [first login](#first-login).

### Installer

```bash
curl -fsSL -O https://raw.githubusercontent.com/retspen/webvirtcloud/master/install.sh
chmod 744 install.sh
sudo ./install.sh
```

The installer writes the server's names and IPs to `ALLOWED_HOSTS`. Running it again keeps `settings.py`, the database, the secret key and the main nginx config, but replaces `/etc/nginx/conf.d/webvirtcloud.conf` and the supervisor config; back up your own lines in those two (TLS, `environment=`) first.

### Docker

```bash
# 1. Clone repository:
git clone https://github.com/retspen/webvirtcloud
cd webvirtcloud

# 2. Set the names/IPs the panel is reached by (git pull never touches this file):
cat > docker-compose.override.yml <<'EOF'
services:
  webvirtcloud:
    environment:
      - ALLOWED_HOSTS=wvc.example.com,192.0.2.10
EOF

# 3. Start services:
docker compose up -d
```

The panel and its console are at `http://<server-ip>`. The database and secret key live in the `webvirtcloud-data` volume, the SSH keys in `webvirtcloud-ssh`. Other settings go in the same `environment:` list, see [Environment variables](#environment-variables).

### Manual installation

Run the steps from a root shell (`sudo -i`). In step 3, replace the example `ALLOWED_HOSTS` with the names/IPs the panel (and the cloud-init datasource) is reached by.

The virtualenv is created with `--system-site-packages`: `libvirt-python` and `python-ldap` come from the distro packages when their version satisfies `conf/requirements.txt`; otherwise pip builds them, which needs the `-dev`/`-devel` packages listed below. Everything else, including `lxml`, comes from pip.

#### Ubuntu 22.04 / 24.04, Debian 12

```bash
# 1. System packages
apt-get update && apt-get -y install git python3-venv python3-dev python3-lxml python3-libvirt libvirt-dev zlib1g-dev libxslt1-dev nginx supervisor libsasl2-modules gcc pkg-config python3-guestfs libsasl2-dev libldap2-dev libssl-dev

# 2. Code
git clone https://github.com/retspen/webvirtcloud /srv/webvirtcloud
cd /srv/webvirtcloud

# 3. Settings, allowed hosts and secret key
cp webvirtcloud/settings.py.template webvirtcloud/settings.py
sed -i 's|^ALLOWED_HOSTS = .*|ALLOWED_HOSTS = ["wvc.example.com", "192.0.2.10", "localhost", "127.0.0.1", "[::1]"]|' webvirtcloud/settings.py
mkdir -p data && python3 conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key

# 4. Virtualenv and dependencies
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r conf/requirements.txt

# 5. Database and static files (the first migrate creates the "admin" user)
python3 manage.py migrate
python3 manage.py collectstatic --noinput

# 6. Supervisor (gunicorn and novncd) and nginx
cp conf/supervisor/webvirtcloud.conf /etc/supervisor/conf.d/
cp conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/
rm -f /etc/nginx/sites-enabled/default

# 7. Ownership, private files, start
chown -R www-data:www-data /srv/webvirtcloud
chmod 700 data && chmod 600 webvirtcloud/settings.py db.sqlite3
systemctl restart nginx supervisor
```

#### RHEL / Rocky Linux / AlmaLinux / Oracle Linux 10

```bash
# 1. System packages
dnf -y install epel-release
dnf -y install git python3-devel libvirt-devel python3-libvirt python3-ldap python3-lxml cyrus-sasl-devel cyrus-sasl-md5 openldap-devel openssl-devel glibc gcc nginx supervisor python3-libguestfs iproute-tc

# 2. Code
git clone https://github.com/retspen/webvirtcloud /srv/webvirtcloud
cd /srv/webvirtcloud

# 3. Settings, allowed hosts and secret key
cp webvirtcloud/settings.py.template webvirtcloud/settings.py
sed -i 's|^ALLOWED_HOSTS = .*|ALLOWED_HOSTS = ["wvc.example.com", "192.0.2.10", "localhost", "127.0.0.1", "[::1]"]|' webvirtcloud/settings.py
mkdir -p data && python3 conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key

# 4. Virtualenv and dependencies
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r conf/requirements.txt

# 5. Database and static files (the first migrate creates the "admin" user)
python3 manage.py migrate
python3 manage.py collectstatic --noinput

# 6. Supervisor (gunicorn and novncd, as nginx) and nginx
sed 's/^user=.*/user=nginx/' conf/supervisor/webvirtcloud.conf > /etc/supervisord.d/webvirtcloud.ini
cp conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/
# make sure the default server block in /etc/nginx/nginx.conf does not also listen on port 80

# 7. Ownership, private files, SELinux, firewall, start
chown -R nginx:nginx /srv/webvirtcloud
chmod 700 data && chmod 600 webvirtcloud/settings.py db.sqlite3
semanage fcontext -a -t httpd_sys_content_t "/srv/webvirtcloud(/.*)" 2>/dev/null || true
restorecon -R /srv/webvirtcloud 2>/dev/null || true
setsebool -P httpd_can_network_connect on 2>/dev/null || true
firewall-cmd --add-service=http --permanent 2>/dev/null || true
firewall-cmd --add-port=6080/tcp --permanent 2>/dev/null || true
firewall-cmd --reload 2>/dev/null || true
systemctl enable nginx supervisord
nginx -t && systemctl restart nginx supervisord
```

#### openSUSE Leap 15.x / Tumbleweed, SLES 15

```bash
# 1. System packages (Python 3.11 stack)
zypper --non-interactive install -y git hostname python311 python311-base python311-devel python311-pip python311-libvirt-python python311-lxml python311-ldap libvirt-devel cyrus-sasl-devel libopenssl-devel gcc pkg-config nginx
zypper --non-interactive install -y python3-supervisor || zypper --non-interactive install -y supervisor

# 2. Code
git clone https://github.com/retspen/webvirtcloud /srv/webvirtcloud
cd /srv/webvirtcloud

# 3. Settings, allowed hosts and secret key
cp webvirtcloud/settings.py.template webvirtcloud/settings.py
sed -i 's|^ALLOWED_HOSTS = .*|ALLOWED_HOSTS = ["wvc.example.com", "192.0.2.10", "localhost", "127.0.0.1", "[::1]"]|' webvirtcloud/settings.py
mkdir -p data && python3.11 conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key

# 4. Virtualenv and dependencies
python3.11 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r conf/requirements.txt

# 5. Database and static files (the first migrate creates the "admin" user)
python3 manage.py migrate
python3 manage.py collectstatic --noinput

# 6. Supervisor (gunicorn and novncd, as nginx) and nginx
mkdir -p /etc/supervisord.d
sed 's/^user=.*/user=nginx/' conf/supervisor/webvirtcloud.conf > /etc/supervisord.d/webvirtcloud.ini
# make sure /etc/supervisord.conf has: [include] files = /etc/supervisord.d/*.ini
cp conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/

# 7. Ownership, private files, start
chown -R nginx:nginx /srv/webvirtcloud
chmod 700 data && chmod 600 webvirtcloud/settings.py db.sqlite3
systemctl enable nginx supervisord
nginx -t && systemctl restart nginx supervisord
```

### First login

The first `migrate` creates a superuser named `admin` with a random password, written to `data/admin_password` (mode 0600):

```bash
sudo cat /srv/webvirtcloud/data/admin_password            # installer or manual installation
docker compose exec webvirtcloud cat data/admin_password   # Docker
```

Sign in at `http://<server-ip>`; you must set a new password at the first login, and the file is then deleted. To choose the name or password yourself, set `ADMIN_USERNAME` and/or `ADMIN_PASSWORD` in the environment before that first `migrate`; a password you set is not written to the file and need not be changed. A lost password is reset with `python3 manage.py changepassword admin`.

## Adding a Compute Node

A compute node is a KVM host the panel manages over libvirt.

1. Install KVM and libvirt. The bootstrap script handles Ubuntu, Debian, CentOS, Fedora, Rocky Linux and AlmaLinux 10, openSUSE and SLES. On RHEL, which needs EPEL from its release RPM, install the `qemu-kvm` and `libvirt` packages yourself and enable `virtqemud.socket` (plus `virtproxyd-tcp.socket` for TCP):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/retspen/webvirtcloud/master/dev/libvirt-bootstrap.sh | sudo sh
   # or, from a cloned repository: sudo sh ./dev/libvirt-bootstrap.sh
   ```

2. For SSH connections, give the user the panel runs as an SSH key and copy it to the compute node. **Use only the block for your installation type:** a key set up with the host commands is not seen by the panel in Docker. `ssh-copy-id` asks for the compute node's root password once. `accept-new` accepts a new host's key on first connect and refuses a changed one.

   **Installer or manual installation**, on the panel host:

   ```bash
   U=www-data   # the web service user: www-data on Debian/Ubuntu, nginx on RHEL/openSUSE
   H=$(getent passwd $U | cut -d: -f6)
   sudo install -d -m 700 -o $U -g $U $H/.ssh
   sudo -u $U ssh-keygen -t ed25519 -N "" -f $H/.ssh/id_ed25519
   printf 'Host *\n  StrictHostKeyChecking accept-new\n' | sudo -u $U tee $H/.ssh/config > /dev/null
   sudo chmod 600 $H/.ssh/config
   sudo -u $U ssh-copy-id root@<compute-node-ip>
   sudo -u $U ssh root@<compute-node-ip> true   # no password or prompt: ready
   ```

   **Docker**, on the Docker host from the directory with `docker-compose.yml`; the commands run inside the container (always as `www-data`: the keys are kept in the `webvirtcloud-ssh` volume, and a key made as root is not used by the panel):

   ```bash
   docker compose exec -u www-data webvirtcloud ssh-keygen -t ed25519 -N "" -f /var/www/.ssh/id_ed25519
   docker compose exec -u www-data webvirtcloud sh -c 'printf "Host *\n  StrictHostKeyChecking accept-new\n" > ~/.ssh/config && chmod 600 ~/.ssh/config'
   docker compose exec -u www-data webvirtcloud ssh-copy-id root@<compute-node-ip>
   docker compose exec -u www-data webvirtcloud ssh root@<compute-node-ip> true   # no password or prompt: ready
   ```

3. Install or update the `gstfsd` daemon on the compute node. It sets the root password and SSH key of a shut-off VM:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/retspen/webvirtcloud/master/conf/daemon/gstfsd | sudo tee /usr/local/bin/gstfsd > /dev/null
   sudo chmod +x /usr/local/bin/gstfsd
   sudo systemctl restart supervisor 2>/dev/null || sudo systemctl restart supervisord
   ```

   It listens on 127.0.0.1:16510 only, which serves a panel on the same host. For a remote panel, uncomment the `environment=GSTFSD_BIND_HOST="0.0.0.0",GSTFSD_ALLOW_REMOTE="1"` line of its supervisor program (`conf/supervisor/gstfsd.conf`) and restart supervisor. It is unauthenticated: allow TCP 16510 only from the panel's IP.

4. Add the compute on the panel's Computes page.

Firewall: the compute's VNC ports (`5900`–`65535`) must accept connections **only** from the panel's IP, never from public networks.

For VM migration between computes, define shared storage as a storage pool on every host (a VM migrates only when its disk, backing and ISO files and block devices are volumes of the destination's pools), and where the hosts cannot resolve each other's names, set each compute's **Migration address** on its edit page.

If libvirt warns `Host SMBIOS information is not available`, install `dmidecode` (`apt-get`, `dnf` or `zypper install -y dmidecode`) and restart `libvirtd`.

## Configuration

### Environment variables

| Variable | Effect |
| --- | --- |
| `ALLOWED_HOSTS` | extra host names/IPs the panel answers to (comma-separated, `*` for any); other hosts get 400 |
| `CSRF_TRUSTED_ORIGINS` | extra trusted origins, e.g. `https://wvc.example.com` |
| `WEBVIRTCLOUD_HTTPS=1` | HTTPS profile, see [HTTPS and reverse proxy](#https-and-reverse-proxy) |
| `WEBVIRTCLOUD_DB_HOST` | use PostgreSQL instead of sqlite (new installs); `WEBVIRTCLOUD_DB_PORT`/`_NAME`/`_USER`/`_PASSWORD` default to 5432/webvirtcloud/webvirtcloud/empty |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | SMTP for e-mail OTP |
| `WEBVIRTCLOUD_LOG_FILE` | log file (default `data/webvirtcloud.log`, rotated at 10 MB × 5) |
| `SECRET_KEY` | Django secret key (default: the `data/secret_key` file) |
| `WS_PUBLIC_PORT`, `WS_PUBLIC_HOST`, `WS_PUBLIC_PATH` | Docker only: public console address; `WS_PUBLIC_PORT` must be the port the panel is published on (default 80) |

Where to set them:

- **Docker:** the `environment:` list of `docker-compose.override.yml`; every process in the container gets it.
- **Installer and manual installation:** edit `webvirtcloud/settings.py`, or put the same `environment=` line in both `[program:webvirtcloud]` and `[program:novncd]` of the supervisor config (novncd reads the database too) and export the variables before running `manage.py`.
- **gunicorn** runs at most 8 workers (fewer on hosts with under 4 CPUs); to change it, set `GUNICORN_CMD_ARGS="--workers N"` in the `[program:webvirtcloud]` environment or in `docker-compose.override.yml`.

### HTTPS and reverse proxy

For a panel served over HTTPS:

- set `WEBVIRTCLOUD_HTTPS=1`: session and CSRF cookies become HTTPS-only, HTTP is redirected to HTTPS (except `/datasource/`, which VMs fetch over HTTP), and HSTS is sent for one year;
- add the HTTPS origin to `CSRF_TRUSTED_ORIGINS`, e.g. `environment=CSRF_TRUSTED_ORIGINS="https://wvc.example.com",WEBVIRTCLOUD_HTTPS="1"` in supervisor;
- set the public noVNC port to the proxy's port (the bundled nginx config already proxies `/novncd/`): `WS_PUBLIC_PORT = 443` in `webvirtcloud/settings.py`, or `WS_PUBLIC_PORT=443` in `docker-compose.override.yml` for Docker;

TLS can end at the bundled nginx or at a proxy in front of it; such a proxy must send `X-Forwarded-Proto: https`. Failed logins are counted per client address, which the bundled nginx passes in `X-Real-IP`. A proxy that replaces the bundled nginx must set `X-Real-IP` itself, and its address must be in `LOGIN_TRUSTED_PROXIES` in `settings.py`. With a proxy in front of the bundled nginx, configure nginx's `set_real_ip_from` / `real_ip_header` for that proxy, or all clients share its address.

### Cloud-init datasource

WebVirtCloud serves cloud-init metadata (root SSH keys and hostname) to guest instances. The host in the URL must be in `ALLOWED_HOSTS`:

```yaml
datasource:
  OpenStack:
    metadata_urls: [ "http://wvc.example.com/datasource" ]
```

### LDAP

The options below are set in `webvirtcloud/settings.py`. Variants for Active Directory and OpenLDAP are shown; this is a minimal config, see the [django-auth-ldap documentation](https://django-auth-ldap.readthedocs.io) for more.

Enable the LDAP backend:

```bash
sudo sed -i "s~#\"django_auth_ldap.backend.LDAPBackend\",~\"django_auth_ldap.backend.LDAPBackend\",~g" /srv/webvirtcloud/webvirtcloud/settings.py
```

Set the LDAP server and bind DN:

```python
# Active Directory
AUTH_LDAP_SERVER_URI = "ldap://example.com"
AUTH_LDAP_BIND_DN = "username@example.com"
AUTH_LDAP_BIND_PASSWORD = "password"

# OpenLDAP
AUTH_LDAP_SERVER_URI = "ldap://example.com"
AUTH_LDAP_BIND_DN = "CN=username,CN=Users,OU=example,OU=com"
AUTH_LDAP_BIND_PASSWORD = "password"
```

Set the user and group search base and filter:

```python
# Active Directory
AUTH_LDAP_USER_SEARCH = LDAPSearch(
    "CN=Users,DC=example,DC=com", ldap.SCOPE_SUBTREE, "(sAMAccountName=%(user)s)"
)
AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
    "CN=Users,DC=example,DC=com", ldap.SCOPE_SUBTREE, "(objectClass=group)"
)
AUTH_LDAP_GROUP_TYPE = NestedActiveDirectoryGroupType()

# OpenLDAP
AUTH_LDAP_USER_SEARCH = LDAPSearch(
    "CN=Users,DC=example,DC=com", ldap.SCOPE_SUBTREE, "(cn=%(user)s)"
)
AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
    "CN=Users,DC=example,DC=com", ldap.SCOPE_SUBTREE, "(objectClass=groupOfUniqueNames)"
)
AUTH_LDAP_GROUP_TYPE = GroupOfUniqueNamesType()  # import needs to be changed at the top of settings.py
```

Set the group required to access WebVirtCloud (`False` disables this filter):

```python
AUTH_LDAP_REQUIRE_GROUP = "CN=WebVirtCloud Access,CN=Users,DC=example,DC=com"
```

Populate user fields from LDAP:

```python
AUTH_LDAP_USER_FLAGS_BY_GROUP = {
    "is_staff": "CN=WebVirtCloud Staff,CN=Users,DC=example,DC=com",
    "is_superuser": "CN=WebVirtCloud Admins,CN=Users,DC=example,DC=com",
}
AUTH_LDAP_USER_ATTR_MAP = {
    "first_name": "givenName",
    "last_name": "sn",
    "email": "mail",
}
```

An LDAP user is authenticated by LDAP and authorized through the WebVirtCloud permissions. To move a user from LDAP to WebVirtCloud, change its password in the UI and remove it from the LDAP group.

### Two-factor login (OTP)

Set `OTP_ENABLED = True` in `settings.py` to ask for a one-time code at login. It is per user: a user without a confirmed device signs in with the password alone.

### Serial console (disabled)

The serial (xterm.js) console is unavailable until `console/socketiod` is rewritten with authentication. Installs from before this change: set `autostart=false` for `[program:socketiod]`, comment out `location /socket.io/` in nginx, and set `SOCKETIO_HOST = "127.0.0.1"` in `settings.py`.

### Running novncd with runit (Debian)

Instead of Supervisor, `novncd` can run under `runit`. First set `autostart=false` for `[program:novncd]` in the supervisor config and run `sudo supervisorctl update`, so the two do not compete for the port:

```bash
sudo apt install -y runit runit-systemd
sudo mkdir -p /etc/service/novncd/
sudo ln -s /srv/webvirtcloud/conf/runit/novncd.sh /etc/service/novncd/run
sudo systemctl start runit.service
```

## Updating

Every update starts with a backup in its own directory (mode 700, named by date) that holds the database, the commit it was taken from and, for installer and manual installations, `settings.py` and the nginx config. The database is copied with SQLite's backup API, which also finishes a write interrupted by a crash. This covers sqlite only; with PostgreSQL (`WEBVIRTCLOUD_DB_HOST`), back up that database yourself before updating.

### Docker

Each new container builds `settings.py` from the template and runs the migrations at start; the database and the secret key stay in the `webvirtcloud-data` volume. Keep your settings in `docker-compose.override.yml` (at least `ALLOWED_HOSTS`, see [Docker](#docker)).

Run the commands in the directory you installed from. Compose names the deployment after that directory (`webvirtcloud`), so another clone in a directory of the same name acts on the same containers and volumes.

```bash
cd webvirtcloud &&
b=../webvirtcloud-backup-$(date +%Y%m%d-%H%M%S) &&
mkdir -m 700 "$b" && git rev-parse HEAD > "$b/commit" &&
git checkout master && git pull &&
docker compose build &&
docker compose stop &&
docker compose run --rm --no-deps -T --entrypoint /srv/webvirtcloud/venv/bin/python3 webvirtcloud \
  -c "import sqlite3,sys,tempfile; t=tempfile.mktemp(); sqlite3.connect('data/db.sqlite3').backup(sqlite3.connect(t)); sys.stdout.buffer.write(open(t,'rb').read())" > "$b/db.sqlite3" &&
docker compose up -d
```

### Installer and manual installation

`git pull` never overwrites `webvirtcloud/settings.py` or `/etc/nginx/conf.d/webvirtcloud.conf`; steps 2 and 4 bring them up to date. Run the steps from a root shell; they use `$b`, so in a new shell set it again to the backup directory.

1. Stop the panel, back up and pull:

   ```bash
   supervisorctl stop webvirtcloud novncd
   # once: root may use the repo, which belongs to the service user
   git config --global --add safe.directory /srv/webvirtcloud
   b=/root/webvirtcloud-backup-$(date +%Y%m%d-%H%M%S)
   cd /srv/webvirtcloud &&
   ! supervisorctl status webvirtcloud novncd | grep RUNNING &&
   mkdir -m 700 "$b" &&
   venv/bin/python3 -c "import sqlite3,sys; sqlite3.connect('db.sqlite3').backup(sqlite3.connect(sys.argv[1]))" "$b/db.sqlite3" &&
   cp -p webvirtcloud/settings.py /etc/nginx/conf.d/webvirtcloud.conf "$b"/ &&
   git rev-parse HEAD > "$b/commit" &&
   git checkout master && git pull
   ```

2. Rebuild `settings.py` from the template (the file keeps its owner and mode), then put back your own values; the `diff` lists them:

   ```bash
   cat webvirtcloud/settings.py.template > webvirtcloud/settings.py
   diff "$b/settings.py" webvirtcloud/settings.py
   ```

   Typical ones are `TIME_ZONE`, `WS_*`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and LDAP. Two need care:
   - a literal `SECRET_KEY = "..."` (older installers wrote it there): put the value into `data/secret_key` instead, or every user is signed out;
   - `SHOW_PROFILE_EDIT_PASSWORD`: keep it for this one `migrate` if you have it (migration `accounts.0008` reads it), then delete it.

3. Install, migrate, and fix ownership and modes (`nginx:nginx` instead of `www-data:www-data` on RHEL / openSUSE):

   ```bash
   venv/bin/pip3 install -U -r conf/requirements.txt &&
   venv/bin/python3 manage.py migrate &&
   venv/bin/python3 manage.py collectstatic --noinput &&
   chown -R www-data:www-data /srv/webvirtcloud &&
   chmod -R go-rwx data webvirtcloud/settings.py db.sqlite3
   ```

4. Update the nginx config the same way as `settings.py`:

   ```bash
   cp conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/webvirtcloud.conf
   diff "$b/webvirtcloud.conf" /etc/nginx/conf.d/webvirtcloud.conf
   ```

   Put back your own lines (`server_name`, TLS, the noVNC port), then reload nginx and start the panel:

   ```bash
   nginx -t && systemctl reload nginx && supervisorctl start webvirtcloud novncd
   ```

Older versions collected static files into `static/`; `git clean -n static/` lists the leftovers, which can be deleted.

### Restore and rollback

Restoring puts back the database as it was at the backup; changes made since are lost. Migrations are not reversed. This covers sqlite; restore a PostgreSQL database from your own backup before rolling back the code. Set `b` to the backup directory to restore. To restore the database without rolling back the code, leave out the `git checkout` and `build` lines.

Docker, in the directory you installed from:

```bash
cd webvirtcloud &&
b=../webvirtcloud-backup-YYYYMMDD-HHMMSS &&
docker compose stop &&
docker compose run --rm --no-deps -T --entrypoint /srv/webvirtcloud/venv/bin/python3 webvirtcloud \
  -c "import os,sqlite3,sys,tempfile; t=tempfile.mktemp(); open(t,'wb').write(sys.stdin.buffer.read()); [os.remove(p) for p in ('data/db.sqlite3-journal','data/db.sqlite3-wal','data/db.sqlite3-shm') if os.path.exists(p)]; sqlite3.connect(t).backup(sqlite3.connect('data/db.sqlite3'))" < "$b/db.sqlite3" &&
git checkout "$(cat "$b/commit")" &&
docker compose build &&
docker compose up -d    # the container fixes ownership and modes at start
```

Installer and manual installation, from a root shell (`nginx:nginx` on RHEL / openSUSE):

```bash
b=/root/webvirtcloud-backup-YYYYMMDD-HHMMSS
supervisorctl stop webvirtcloud novncd
cd /srv/webvirtcloud &&
! supervisorctl status webvirtcloud novncd | grep RUNNING &&
git checkout "$(cat "$b/commit")" &&
venv/bin/python3 -c "import os,sqlite3,sys; [os.remove(p) for p in ('db.sqlite3-journal','db.sqlite3-wal','db.sqlite3-shm') if os.path.exists(p)]; sqlite3.connect(sys.argv[1]).backup(sqlite3.connect('db.sqlite3'))" "$b/db.sqlite3" &&
cp "$b/settings.py" webvirtcloud/settings.py &&
cp "$b/webvirtcloud.conf" /etc/nginx/conf.d/webvirtcloud.conf &&
venv/bin/pip3 install -r conf/requirements.txt &&
venv/bin/python3 manage.py collectstatic --noinput &&
chown -R www-data:www-data /srv/webvirtcloud &&
chmod 600 db.sqlite3 webvirtcloud/settings.py &&
nginx -t && systemctl reload nginx &&
supervisorctl start webvirtcloud novncd
```

A rollback leaves the clone on a detached HEAD at the restored commit; the update steps record that commit before they switch back to `master`.

### Behavior changes

- Requests for a host not in `ALLOWED_HOSTS` get 400. The installer writes the server's names and IPs; add more with the `ALLOWED_HOSTS` env var. The cloud-init datasource host must be allowed too.
- Logging out needs a POST; a GET to `/accounts/logout/` gets 405.
- The API accepts only a session from the login form (no HTTP Basic), and the API docs need a login. Sessions opened through the old password-only doors skip OTP; end them once after the update (everyone signs in again): `python3 manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete()"`.
- Failed logins lock a user (django-axes). The limit and lock time are on the Settings page; unlock with `python3 manage.py axes_reset_username <username>`.
- Changing one's own password is the "Can change password" permission (per user or group; new users have it); `SHOW_PROFILE_EDIT_PASSWORD` is gone.
- The admin's first-login password change survives losing `data/admin_password` (migration `accounts.0007`); run `migrate` as a user who can read that file.
- "VM Clone Auto Migrate" is removed; a clone stays on its source host.
- A template VM is changed or deleted only by superusers and staff owners with the matching permission; other owners can still view, open the console and clone it. Power cycle no longer starts a template.
- A VM that vanishes from its compute keeps its owners for `INSTANCE_OWNERSHIP_RETENTION_DAYS` (30) in case the same UUID comes back; superusers list and remove these records on the "Removed VMs" admin page.
- The log rotates (10 MB × 5) in `data/webvirtcloud.log`.
- A disk can be grown while its VM runs or is paused, not only when it is shut off.
- Enabling vCPU hot plug keeps the VM's current vCPU count (it enabled all of them up to the maximum).
- A host's CPU graph shows the average since its previous point (normally 5 s) instead of a 1 s sample taken while a worker waits.
- The CPU usage in the new-VM dialog is the current load (over 1 s), read when the dialog opens; it was the average since the host booted.
- A VM's stats are polled every 5 s like the host graph, the first point as soon as the tab opens; each point is the average since the previous one, disk rates in MB/s (they were MiB/s labelled Mb/s); a counter the host does not report shows a gap instead of 0.
- An ISO uploads to an SSH compute about 3.5 times faster (SFTP writes no longer wait for each 32 KiB); a failed write still fails the upload.
- On a host whose libvirt cannot change network interfaces (the udev backend: RHEL 9 and later, Debian 12), the interface pages offer no create, start, stop or delete; they only failed there.
- On a running VM the disk list, its edit form and the QoS table show the settings as they will be at the next start, so saving an edit no longer reverts another pending change. A QoS edit keeps the inbound floor.
- The VM page shows each NIC's own QoS and each CD-ROM's own pool (they showed the previous device's), and a NIC without a source or model no longer breaks it.
- A volume of an LVM or iSCSI pool can be attached to a VM, and bridge and direct NICs can be boot devices. The NIC model `default` lets libvirt choose, and `rt18139` is `rtl8139`: a VM with either did not start; a migration corrects the default NIC type setting.
- A paused VM's memory is resized as a running VM's: the current memory changes and the guest takes it once resumed, the maximum stays (both only reached the VM at its next boot).
- Cloning a UEFI VM no longer defines a storage pool on the host's NVRAM directory; the clone gets its own copy of the VM's UEFI variables.
- The console .vv file finds the VM by its UUID; without a listen address or password it gives 127.0.0.1 and an empty password instead of "None".
- A running VM without a guest agent shows the IPv4 address the host learned by ARP (the lookup always failed before).
- After a live migration the VM's NVRAM file is deleted on the source, when it is in libvirt's `/var/lib/libvirt/qemu/nvram` and the destination does not share that directory (a VM created there later with the same name would start with those UEFI variables).
- gstfsd finds the guest's operating system the way libguestfs inspects it, so a root on LVM works, and it always answers (an error when it finds no or several operating systems). Update `/usr/local/bin/gstfsd` on every compute (see [Adding a Compute Node](#adding-a-compute-node), step 3).
- Docker no longer publishes port 6080; the console goes through nginx on the panel's port (`WS_PUBLIC_PORT`, default 80).

## Usage

### Users, Roles and Permissions

See [doc/permissions.md](doc/permissions.md).

### REST API

The REST API (Django REST Framework, documented with `drf-spectacular`) uses the login session. After logging in:

* **Swagger UI:** `http://<server>/swagger/`
* **ReDoc UI:** `http://<server>/redoc/`
* **OpenAPI 3.0 schema:** `http://<server>/api/schema/` (JSON or YAML)

### Screenshots

| Instance Detail |
|:---:|
| ![Instance Detail](doc/images/instance.PNG) |

| Grouped Instances | Non-Grouped Instances |
|:---:|:---:|
| ![Grouped Instances](doc/images/grouped.PNG) | ![Non-Grouped Instances](doc/images/nongrouped.PNG) |

| Compute Hosts | Activity Log |
|:---:|:---:|
| ![Compute Hosts](doc/images/hosts.PNG) | ![Activity Log](doc/images/log.PNG) |

## Development

Run every step as your normal user, not with `sudo`: if `manage.py` ever runs as root, `db.sqlite3` and `data/` end up root-owned and later runs fail with "readonly database" or "Permission denied".

1. System packages. The virtualenv reuses the distro's `libvirt`, `lxml` and `ldap` bindings when their versions satisfy `conf/requirements.txt`; otherwise pip installs or builds newer ones:

   ```bash
   # Rocky Linux / RHEL / Fedora
   sudo dnf -y install git python3-devel libvirt-devel python3-libvirt python3-lxml python3-ldap gcc

   # Ubuntu / Debian
   sudo apt-get update && sudo apt-get -y install git python3-venv python3-dev python3-lxml python3-libvirt python3-ldap libvirt-dev zlib1g-dev libldap2-dev libsasl2-dev gcc pkg-config

   # openSUSE Leap 15.x / Tumbleweed / SLES 15 (use python3.11 instead of python3 below)
   sudo zypper --non-interactive install -y git hostname python311 python311-devel python311-pip python311-libvirt-python python311-lxml python311-ldap libvirt-devel cyrus-sasl-devel libopenssl-devel gcc pkg-config
   ```

2. Virtualenv and dependencies (`--system-site-packages` is required to see the distro bindings; `dev/requirements.txt` includes `conf/requirements.txt`):

   ```bash
   python3 -m venv --system-site-packages .venv
   source .venv/bin/activate
   pip install -r dev/requirements.txt
   ```

3. Settings and secret key:

   ```bash
   cp webvirtcloud/settings.py.template webvirtcloud/settings.py
   mkdir -p data && python conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key
   ```

4. Migrate and run the dev server (`settings-dev` enables `DEBUG` and the Django Debug Toolbar); the admin password is in `data/admin_password`, or set `ADMIN_PASSWORD` beforehand:

   ```bash
   python manage.py migrate
   python manage.py runserver 0.0.0.0:8000 --settings=webvirtcloud.settings-dev
   ```

   Open `http://127.0.0.1:8000`. For the browser console, run `python console/novncd` (port 6080) in a separate terminal.

### Running tests

```bash
python manage.py test   # all tests, including vrtManager
ruff check .
```

The suite includes mocked regression tests and disk/clone tests on libvirt's in-process `test:///default` driver. Set `WEBVIRTCLOUD_DB_HOST` (and the other `WEBVIRTCLOUD_DB_*` variables) to run it against PostgreSQL, as CI does.

The compute tests try a local libvirt and skip when none answers; the instance tests run only when `TEST_LIBVIRT_HOST` is set. A green result with skips does not establish live KVM compatibility. The instance tests create `wvc-test-*` VMs and a temporary `wvc-test` pool (`/var/lib/libvirt/wvc-test`), remove them afterwards, and fail if any other VM or volume on the host changed:

```bash
export TEST_LIBVIRT_HOST=compute1 TEST_LIBVIRT_TYPE=2   # 1 TCP, 2 SSH, 3 TLS, 4 socket
export TEST_LIBVIRT_LOGIN=root TEST_LIBVIRT_PASSWORD=   # login/password for TCP and TLS
export TEST_LIBVIRT_GUEST_IMAGE=/var/lib/libvirt/images/linux.qcow2   # optional, see below
python manage.py test
```

`TEST_LIBVIRT_GUEST_IMAGE` enables the tests that need a guest OS (disk and NIC hot-unplug, vCPU hotplug, guest agent, ACPI power off). It must be an installed, BIOS-bootable Linux qcow2 image with qemu-guest-agent, in a storage pool of the host other than `wvc-test`; the tests boot copies of it from overlays and only read the image. Shut down any VM using it first.

## License

WebVirtCloud is licensed under the [Apache Licence, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.html).
