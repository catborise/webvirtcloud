[![Gitpod ready-to-code](https://img.shields.io/badge/Gitpod-ready--to--code-blue?logo=gitpod)](https://gitpod.io/#https://github.com/retspen/webvirtcloud)

# WebVirtCloud
###### Python >=3.10 & Django 5.2 LTS (tested on Python 3.10 – 3.13)

## Purpose and Scope

WebVirtCloud is a **web visualization and management layer** for **libvirt**-based virtualization infrastructure.

### Core Design Principle

> **libvirt is the single source of truth.** WebVirtCloud reads, displays and triggers actions on libvirt state, but it is never the primary orchestrator.

This means:

- An administrator can connect to libvirt directly with **`virt-manager`** or **`virsh`** and create, delete, rename or reconfigure virtual machines. WebVirtCloud detects these changes and updates its database accordingly.
- Virtual machines keep running **even when WebVirtCloud is stopped**; the application only reflects the current state of libvirt.
- WebVirtCloud is not a full automation platform like Proxmox. Its goal is to expose libvirt's functionality through the browser in an accessible, role-based and secure way.

### Architecture

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

---

## Features

* QEMU/KVM hypervisor management (multiple compute nodes)
* Virtual machine lifecycle: create, delete, power management, clone
* **External change detection:** VMs created, deleted or renamed via `virt-manager` or `virsh` are reconciled into the database automatically
* Real-time hypervisor and VM statistics (CPU, RAM, disk, network)
* Storage pool and volume management
* Network and interface management
* Browser-based consoles: noVNC (VNC) and xterm.js (serial/PTY)
* Role-based access control: users only see the VMs assigned to them
* 2FA (OTP / TOTP) support
* SSH public key and root password management (via guestfs)
* Cloud-init datasource interface (OpenStack metadata compatible)
* REST API — OpenAPI 3.0 (Swagger & ReDoc)
* LDAP / Active Directory integration (optional)



## Quick Install with Installer (Beta)

Install an OS and run specified commands. Installer supported OSes: Ubuntu 20.04/22.04/24.04, Debian 10/11/12, Rocky/Alma/OEL/RHEL 9/10, openSUSE Leap 15.x / Tumbleweed, and SLES 15.
It can be installed on a virtual machine, physical host or on a KVM host.

```bash
# Using curl:
curl -fsSL -O https://raw.githubusercontent.com/retspen/webvirtcloud/master/install.sh
# Or using wget:
# wget https://raw.githubusercontent.com/retspen/webvirtcloud/master/install.sh

chmod 744 install.sh
# run with sudo or root user
./install.sh
```

## Docker Deployment (Docker Compose)

Run WebVirtCloud in a container with persistent volumes for data and SSH keys:

```bash
# 1. Clone repository:
git clone https://github.com/retspen/webvirtcloud
cd webvirtcloud

# 2. Start services:
docker compose up -d
```

Access the panel at `http://<server-ip>` and noVNC console at port `6080`.

## Manual Installation

The steps below work inside `/srv/webvirtcloud`, which stays root-owned until the final `chown`. Run them from a root shell (`sudo -i`).

The virtualenv is created with `--system-site-packages`: `libvirt-python` and `python-ldap` come from the distro packages when their version satisfies `conf/requirements.txt`; otherwise pip builds them, which needs the `-dev`/`-devel` packages listed below. Everything else, including `lxml`, comes from pip.

### Secret key

`webvirtcloud/settings.py` reads `SECRET_KEY` from the `SECRET_KEY` environment variable, falling back to the `data/secret_key` file (the `data/` directory is gitignored). The steps below generate that file:

```bash
mkdir -p data
python3 conf/runit/secret_generator.py > data/secret_key
chmod 600 data/secret_key
```

### Ubuntu 20.04 / 22.04 / 24.04 LTS & Debian 11 / 12

```bash
# 1. Install system prerequisites
sudo apt-get update && sudo apt-get -y install git python3-venv python3-dev python3-lxml python3-libvirt libvirt-dev zlib1g-dev libxslt1-dev nginx supervisor libsasl2-modules gcc pkg-config python3-guestfs libsasl2-dev libldap2-dev libssl-dev

# 2. Clone repository to /srv/webvirtcloud
sudo git clone https://github.com/retspen/webvirtcloud /srv/webvirtcloud
cd /srv/webvirtcloud

# 3. Configure settings and secret key
cp webvirtcloud/settings.py.template webvirtcloud/settings.py
mkdir -p data && python3 conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key

# 4. Deploy service configurations
sudo cp conf/supervisor/webvirtcloud.conf /etc/supervisor/conf.d/
sudo cp conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/
sudo rm -f /etc/nginx/sites-enabled/default

# 5. Create virtual environment and install dependencies
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r conf/requirements.txt

# 6. Database migrations and static files
# (the first migrate creates the "admin" user; its password is in data/admin_password)
python3 manage.py migrate
python3 manage.py collectstatic --noinput

# 7. Set permissions and start services
sudo chown -R www-data:www-data /srv/webvirtcloud
sudo systemctl restart nginx supervisor
```

---

### RHEL 8 / 9 / 10 / Rocky Linux / AlmaLinux

```bash
# 1. Install EPEL and system prerequisites
sudo dnf -y install epel-release
sudo dnf -y install git python3-devel libvirt-devel python3-libvirt python3-ldap python3-lxml cyrus-sasl-devel cyrus-sasl-md5 openldap-devel openssl-devel glibc gcc nginx supervisor python3-libguestfs iproute-tc

# 2. Clone repository to /srv/webvirtcloud
sudo git clone https://github.com/retspen/webvirtcloud /srv/webvirtcloud
cd /srv/webvirtcloud

# 3. Configure settings and secret key
cp webvirtcloud/settings.py.template webvirtcloud/settings.py
mkdir -p data && python3 conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key

# 4. Create virtual environment and install dependencies
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r conf/requirements.txt

# 5. Database migrations and static files
# (the first migrate creates the "admin" user; its password is in data/admin_password)
python3 manage.py migrate
python3 manage.py collectstatic --noinput

# 6. Configure Supervisor (gunicorn and novncd, running as nginx; socketiod is disabled by default)
sed 's/^user=.*/user=nginx/' conf/supervisor/webvirtcloud.conf | sudo tee /etc/supervisord.d/webvirtcloud.ini > /dev/null

# 7. Configure Nginx
sudo cp conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/
# Ensure the default server block in /etc/nginx/nginx.conf does not conflict with webvirtcloud.conf

# 8. Set permissions, SELinux, and Firewall
sudo chown -R nginx:nginx /srv/webvirtcloud
sudo semanage fcontext -a -t httpd_sys_content_t "/srv/webvirtcloud(/.*)" 2>/dev/null || true
sudo restorecon -R /srv/webvirtcloud 2>/dev/null || true
sudo setsebool -P httpd_can_network_connect on 2>/dev/null || true

sudo firewall-cmd --add-service=http --permanent 2>/dev/null || true
sudo firewall-cmd --add-port=6080/tcp --permanent 2>/dev/null || true
sudo firewall-cmd --reload 2>/dev/null || true

# 9. Start and enable services
sudo systemctl enable --now nginx supervisord
sudo systemctl restart nginx supervisord
```

---

### openSUSE Leap 15.x / Tumbleweed / SLES 15

```bash
# 1. Install system prerequisites (Python 3.11 stack and C bindings)
sudo zypper --non-interactive install -y git hostname python311 python311-base python311-devel python311-pip python311-libvirt-python python311-lxml python311-ldap libvirt-devel cyrus-sasl-devel libopenssl-devel gcc pkg-config nginx

# 2. Clone repository to /srv/webvirtcloud
sudo git clone https://github.com/retspen/webvirtcloud /srv/webvirtcloud
cd /srv/webvirtcloud

# 3. Configure settings and secret key
cp webvirtcloud/settings.py.template webvirtcloud/settings.py
mkdir -p data && python3.11 conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key

# 4. Create virtual environment and install dependencies
python3.11 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r conf/requirements.txt

# 5. Database migrations and static files
# (the first migrate creates the "admin" user; its password is in data/admin_password)
python3 manage.py migrate
python3 manage.py collectstatic --noinput

# 6. Configure Nginx
sudo cp conf/nginx/suse_nginx.conf /etc/nginx/vhosts.d/webvirtcloud.conf 2>/dev/null || sudo cp conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/

# 7. Configure Supervisor (gunicorn and novncd, running as nginx; socketiod is disabled by default)
sudo zypper --non-interactive install -y python3-supervisor || sudo zypper --non-interactive install -y supervisor
sudo mkdir -p /etc/supervisord.d
sed 's/^user=.*/user=nginx/' conf/supervisor/webvirtcloud.conf | sudo tee /etc/supervisord.d/webvirtcloud.ini > /dev/null
# Make sure /etc/supervisord.conf has: [include] files = /etc/supervisord.d/*.ini

# 8. Set permissions and start services
sudo chown -R nginx:nginx /srv/webvirtcloud
sudo systemctl enable --now nginx supervisord
sudo systemctl restart nginx supervisord
```

---

## Local Development Setup

For developers working locally on WebVirtCloud without running full production services. Run every step as your normal user, not with `sudo`: if `manage.py` ever runs as root, `db.sqlite3` and `data/` end up root-owned and later runs fail with "readonly database" or "Permission denied".

### 1. Install system packages

The virtualenv reuses the distro's prebuilt `libvirt`, `lxml` and `ldap` bindings, so nothing needs compiling.

```bash
# Rocky Linux / RHEL / Fedora
sudo dnf -y install git python3-devel libvirt-devel python3-libvirt python3-lxml python3-ldap gcc

# Ubuntu / Debian
sudo apt-get update && sudo apt-get -y install git python3-venv python3-dev python3-lxml python3-libvirt python3-ldap libvirt-dev zlib1g-dev libldap2-dev libsasl2-dev gcc pkg-config

# openSUSE Leap 15.x / Tumbleweed / SLES 15 (use python3.11 instead of python3 in the steps below)
sudo zypper --non-interactive install -y git hostname python311 python311-devel python311-pip python311-libvirt-python python311-lxml python311-ldap libvirt-devel cyrus-sasl-devel libopenssl-devel gcc pkg-config
```

### 2. Create the virtualenv and install dependencies

`--system-site-packages` is required; without it the venv cannot see the distro bindings above. `dev/requirements.txt` includes `conf/requirements.txt`.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r dev/requirements.txt
```

### 3. Configure settings and secret key

```bash
cp webvirtcloud/settings.py.template webvirtcloud/settings.py
mkdir -p data && python conf/runit/secret_generator.py > data/secret_key && chmod 600 data/secret_key
```

### 4. Migrate and run the dev server

```bash
# The first migrate creates the "admin" user; its password is in data/admin_password.
# Set ADMIN_PASSWORD beforehand to choose it yourself.
python manage.py migrate

python manage.py runserver 0.0.0.0:8000 --settings=webvirtcloud.settings-dev --nostatic
```

Open `http://127.0.0.1:8000`. `settings-dev` enables `DEBUG` and the Django Debug Toolbar. `--nostatic` is required: the CSS/JS live in `static/` (`STATIC_ROOT`), which WhiteNoise serves but runserver's own static handler does not.

For the browser console, run `python console/novncd` (VNC, port 6080) in a separate terminal.

## Compute Node (Hypervisor) Setup

To configure a physical server or virtual machine as a KVM compute node to be managed by WebVirtCloud:

### 1. Install KVM and Libvirt via Bootstrap Script

WebVirtCloud includes an automated bootstrap script supporting Ubuntu 20.04/22.04/24.04, Debian 10/11/12, RHEL/Rocky/Alma 8/9/10, openSUSE Leap 15.x / Tumbleweed, and SLES 15:

```bash
# Run bootstrap script directly via curl:
curl -fsSL https://raw.githubusercontent.com/retspen/webvirtcloud/master/dev/libvirt-bootstrap.sh | sudo sh

# Or run locally from a cloned repository:
sudo ./dev/libvirt-bootstrap.sh
```

### 2. Configure SSH Connection Between Panel and Compute Node

On the WebVirtCloud panel host, generate an SSH key for the web service user (`www-data` on Debian/Ubuntu, `nginx` on RHEL/openSUSE):

```bash
# Generate key (Debian/Ubuntu example using www-data):
sudo -u www-data ssh-keygen -t ed25519
sudo -u www-data tee ~www-data/.ssh/config > /dev/null << 'EOF'
Host *
  StrictHostKeyChecking no
EOF
sudo chmod 600 ~www-data/.ssh/config

# Copy public key to the compute node root user:
sudo -u www-data ssh-copy-id root@<compute-node-ip>
```

### 3. Install or Update `gstfsd` Daemon

The `gstfsd` daemon sets the root password and SSH key of a shut-off VM. It listens on 127.0.0.1 only; for a remote compute set `GSTFSD_BIND_HOST` and `GSTFSD_ALLOW_REMOTE=1` (it is unauthenticated, so trusted networks only):

```bash
curl -fsSL https://raw.githubusercontent.com/retspen/webvirtcloud/master/conf/daemon/gstfsd | sudo tee /usr/local/bin/gstfsd > /dev/null
sudo chmod +x /usr/local/bin/gstfsd
sudo systemctl restart supervisor 2>/dev/null || sudo systemctl restart supervisord
```

### 4. Troubleshooting: Host SMBIOS Warning

If you see the warning `Unsupported configuration: Host SMBIOS information is not available`, install `dmidecode` and restart libvirt:

```bash
# Debian / Ubuntu:
sudo apt-get install -y dmidecode && sudo systemctl restart libvirtd

# RHEL / Rocky / AlmaLinux:
sudo dnf install -y dmidecode && sudo systemctl restart libvirtd

# openSUSE / SLES:
sudo zypper install -y dmidecode && sudo systemctl restart libvirtd
```

> **Security Notice (Compute Node Firewall):**
> Libvirt compute nodes listen on VNC ports (`5900`–`65535`) to allow WebVirtCloud to proxy graphical consoles. Ensure your firewall (`ufw`, `firewalld`, or `iptables`) restricts these ports to accept connections **only** from the WebVirtCloud panel IP, and never exposes them directly to public networks.

---

## Configuration & Operational Notes

### Default Credentials

The first `python3 manage.py migrate` on an empty database creates a superuser named `admin` with a random password, written to `data/admin_password` (mode 0600). In Docker: `docker compose exec webvirtcloud cat data/admin_password`. At the first login with it you must set a new password; the file is then deleted.

Set `ADMIN_USERNAME` and/or `ADMIN_PASSWORD` in the environment before that first migrate to choose them yourself. Then sign in at `http://<server-ip>`.

If you lose the password, reset it with `python3 manage.py changepassword admin`.

### Alternative: Running novncd via runit (Debian)

As an alternative to Supervisor, Debian systems can manage `novncd` via `runit`:

```bash
sudo apt install -y runit runit-systemd
sudo mkdir -p /etc/service/novncd/
sudo ln -s /srv/webvirtcloud/conf/runit/novncd.sh /etc/service/novncd/run
sudo systemctl start runit.service
```

### Cloud-Init Datasource

WebVirtCloud can serve cloud-init metadata (root SSH keys and hostname) to guest instances:

```yaml
datasource:
  OpenStack:
    metadata_urls: [ "http://webvirtcloud.domain.com/datasource" ]
```

### Serial Console (disabled)

The serial (xterm.js) console is unavailable until `console/socketiod` is rewritten with authentication. Existing installs: set `autostart=false` for `[program:socketiod]`, comment out `location /socket.io/` in nginx, and set `SOCKETIO_HOST = "127.0.0.1"` in `settings.py`.

### Reverse-Proxy & Port Forwarding

If WebVirtCloud runs behind a reverse proxy terminating SSL or forwarding port 80/443, set the public noVNC port in `webvirtcloud/settings.py` (default 6080). The bundled nginx config already proxies `/novncd/`:

```python
WS_PUBLIC_PORT = 80  # or 443
```

When the panel is served over HTTPS on a hostname other than localhost, add it to the trusted CSRF origins (comma-separated) through the environment, e.g. in the `[program:webvirtcloud]` block of the supervisor config:

```ini
environment=CSRF_TRUSTED_ORIGINS="https://webvirtcloud.example.com"
```

## How To Update

Back up `db.sqlite3` before updating (in Docker it lives in the `data` volume).

Upgrading from Django 4.2, edit `webvirtcloud/settings.py`:

- in `MIDDLEWARE`, replace `"login_required.middleware.LoginRequiredMiddleware"` with `"django.contrib.auth.middleware.LoginRequiredMiddleware"` (the old package is no longer a dependency; `migrate` refuses to run until this is done);
- delete `LOGIN_REQUIRED_IGNORE_VIEW_NAMES` and `USE_L10N`.

Logging out now needs a POST; links or scripts that call `/accounts/logout/` with GET get 405.

Before running migrations, add `accounts.middleware.ForcePasswordChangeMiddleware`
to `MIDDLEWARE` in your existing `webvirtcloud/settings.py`, after
`django.contrib.auth.middleware.AuthenticationMiddleware` (and the OTP/login-required
middleware, if present). A system check rejects a missing or misplaced middleware.
The template is not copied over an existing settings file during an upgrade.

Migration `accounts.0007` transfers the first-login requirement from any existing
`data/admin_password` file to the user record. Run migrations as a user who can read
that file. Thereafter, deleting or losing access to the file does not bypass the
requirement. If provisioning cannot write a new private password file, it stops
without creating an admin account or printing its password; fix the directory
permissions and rerun migrations.

```bash
# Go to Installation Directory
cd /srv/webvirtcloud
source venv/bin/activate
git pull
pip3 install -U -r conf/requirements.txt
python3 manage.py migrate
python3 manage.py collectstatic --noinput
sudo systemctl restart supervisor    # supervisord on RHEL / openSUSE
```

> **Note on Settings Upgrade:**
> When upgrading from earlier versions using `drf-yasg`, update your `webvirtcloud/settings.py`:
> 1. In `INSTALLED_APPS`, replace `'drf_yasg'` with `'drf_spectacular'` and `'drf_spectacular_sidecar'`.
> 2. Ensure the `REST_FRAMEWORK` and `SPECTACULAR_SETTINGS` configuration blocks are present (see `webvirtcloud/settings.py.template`).

## Running Tests

WebVirtCloud includes unit tests for both Django models/views and the `vrtManager` libvirt abstraction layer. The suite includes mocked regression tests and disk/clone tests using libvirt's
in-process `test:///default` driver. The live compute and instance integration
tests still require a configured libvirt host and are skipped when it is unavailable;
a green result with skips does not establish live KVM compatibility.

### 1. Setup Virtual Environment
Use the same virtualenv as in [Local Development Setup](#local-development-setup), steps 1–3. Tests need the secret key too.
```bash
source .venv/bin/activate
```

### 2. Run Test Suite and Linter
```bash
# All tests, including vrtManager:
python manage.py test

ruff check .
```

> **Live Hypervisor Testing (Optional):**
> The instance tests run only when `TEST_LIBVIRT_HOST` is set. They create `wvc-test-*` VMs and a temporary `wvc-test` pool (`/var/lib/libvirt/wvc-test`), remove them afterwards, and fail if any other VM or volume on the host changed.
> ```bash
> export TEST_LIBVIRT_HOST=compute1 TEST_LIBVIRT_TYPE=2   # 1 TCP, 2 SSH, 3 TLS, 4 socket
> export TEST_LIBVIRT_LOGIN=root TEST_LIBVIRT_PASSWORD=   # login/password for TCP and TLS
> python manage.py test
> ```

## Users, Roles and Permissions

See [doc/permissions.md](doc/permissions.md).

## LDAP Configuration

The config options below can be changed in `webvirtcloud/settings.py` file. Variants for Active Directory and OpenLDAP are shown. This is a minimal config to get LDAP running, for further info read the [django-auth-ldap documentation](https://django-auth-ldap.readthedocs.io).

Enable LDAP

```bash
sudo sed -i "s~#\"django_auth_ldap.backend.LDAPBackend\",~\"django_auth_ldap.backend.LDAPBackend\",~g" /srv/webvirtcloud/webvirtcloud/settings.py
```

Set the LDAP server name and bind DN

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

Set the user filter and user and group search base and filter

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

Set group which is required to access WebVirtCloud. You may set this to `False` to disable this filter.

```python
AUTH_LDAP_REQUIRE_GROUP = "CN=WebVirtCloud Access,CN=Users,DC=example,DC=com"
```

Populate user fields with values from LDAP

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

Now when you login with an LDAP user it will be assigned the rights defined. The user will be authenticated then with LDAP and authorized through the WebVirtCloud permissions.

If you'd like to move a user from ldap to WebVirtCloud, just change its password from the UI and (eventually) remove from the group in LDAP.


## REST API (OpenAPI 3.0)

WebVirtCloud provides a REST API powered by Django REST Framework and documented via `drf-spectacular`.

You can access the interactive API documentation and schema endpoints in your browser:

* **Swagger UI:** `http://<webvirtcloud-address:port>/swagger/`
* **ReDoc UI:** `http://<webvirtcloud-address:port>/redoc/`
* **OpenAPI 3.0 Schema:** `http://<webvirtcloud-address:port>/api/schema/` (download schema in JSON or YAML format)

## Screenshots

| Instance Detail |
|:---:|
| ![Instance Detail](doc/images/instance.PNG) |

| Grouped Instances | Non-Grouped Instances |
|:---:|:---:|
| ![Grouped Instances](doc/images/grouped.PNG) | ![Non-Grouped Instances](doc/images/nongrouped.PNG) |

| Compute Hosts | Activity Log |
|:---:|:---:|
| ![Compute Hosts](doc/images/hosts.PNG) | ![Activity Log](doc/images/log.PNG) |

## License

WebVirtCloud is licensed under the [Apache Licence, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.html).
