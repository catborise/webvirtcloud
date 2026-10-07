FROM phusion/baseimage:noble-1.0.2 AS build

# Compilers and headers for libvirt-python and python-ldap; they stay in this stage.
# hadolint ignore=DL3008
RUN apt-get update -qqy \
    && DEBIAN_FRONTEND=noninteractive apt-get -qyy install \
	--no-install-recommends \
	python3-venv \
	python3-dev \
	libvirt-dev \
	zlib1g-dev \
	pkg-config \
	gcc \
	libldap2-dev \
	libssl-dev \
	libsasl2-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /srv/webvirtcloud
COPY conf/requirements.txt conf/requirements.txt
# hadolint ignore=DL3013,DL3042
RUN python3 -m venv venv \
    && venv/bin/pip install --no-cache-dir -U pip wheel \
    && venv/bin/pip install --no-cache-dir -r conf/requirements.txt

FROM phusion/baseimage:noble-1.0.2

EXPOSE 80

# The browser reaches the console through nginx on the panel's port (the init
# script writes this into settings.py); set it to the published port if not 80.
ENV WS_PUBLIC_PORT=80

# Use baseimage-docker's init system.
CMD ["/sbin/my_init"]

RUN echo 'APT::Get::Clean=always;' >> /etc/apt/apt.conf.d/99AutomaticClean

# Shared libraries of the compiled wheels, and nginx; no compilers or headers.
# hadolint ignore=DL3008
RUN apt-get update -qqy \
    && DEBIAN_FRONTEND=noninteractive apt-get -qyy install \
	--no-install-recommends \
	python3 \
	nginx \
	libvirt0 \
	libsasl2-modules \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /srv/webvirtcloud
COPY --from=build /srv/webvirtcloud/venv venv
COPY . /srv/webvirtcloud

# Run collectstatic with temporary dummy key, then remove temporary settings file
RUN cp webvirtcloud/settings.py.template webvirtcloud/settings.py && \
	SECRET_KEY="build-dummy-key-only-for-collectstatic" venv/bin/python3 manage.py collectstatic --noinput && \
	rm -f webvirtcloud/settings.py && \
	chown -R www-data:www-data /srv/webvirtcloud

# Setup Nginx
RUN printf "\n%s" "daemon off;" >> /etc/nginx/nginx.conf && \
	rm -f /etc/nginx/sites-enabled/default && \
	chown -R www-data:www-data /var/lib/nginx

COPY conf/nginx/webvirtcloud.conf /etc/nginx/conf.d/

# Register startup init script and services to runit
RUN mkdir -p /etc/my_init.d \
	/etc/service/nginx \
	/etc/service/nginx-log-forwarder \
	/etc/service/webvirtcloud \
	/etc/service/novnc
COPY conf/runit/10_webvirtcloud_init.sh	/etc/my_init.d/10_webvirtcloud_init.sh
COPY conf/runit/nginx				/etc/service/nginx/run
COPY conf/runit/nginx-log-forwarder	/etc/service/nginx-log-forwarder/run
COPY conf/runit/novncd.sh			/etc/service/novnc/run
COPY conf/runit/webvirtcloud.sh		/etc/service/webvirtcloud/run
RUN chmod +x /etc/my_init.d/10_webvirtcloud_init.sh \
	/etc/service/nginx/run \
	/etc/service/nginx-log-forwarder/run \
	/etc/service/novnc/run \
	/etc/service/webvirtcloud/run

# X-Forwarded-Proto: https keeps the probe from being redirected when the
# HTTPS profile is on (nginx passes the header on; see conf/nginx).
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
	CMD /srv/webvirtcloud/venv/bin/python3 -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1/accounts/login/', headers={'X-Forwarded-Proto': 'https'}), timeout=5)"

# Declare mountable data directory for persistent SQLite and SSH keys
VOLUME ["/srv/webvirtcloud/data", "/var/www/.ssh"]

WORKDIR /srv/webvirtcloud
