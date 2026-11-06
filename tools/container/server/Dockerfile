# A stand-in Ubuntu server for tools/container/server_test.sh: systemd as
# PID 1, sshd, and a plain user `shop` with no sudo -- the headless server
# packaging/linux/install.sh is written for. Nothing of AutoRundown is in
# the image: the test copies a snapshot of the checkout in and runs the
# script over SSH, as a person would.
ARG UBUNTU=26.04
FROM ubuntu:${UBUNTU}

ENV DEBIAN_FRONTEND=noninteractive
# what the README tells a person to install first (git, python3-venv), plus
# what an Ubuntu server has anyway (systemd, sshd, dbus and libpam-systemd:
# an SSH login gets a user systemd session, which `systemctl --user` needs)
RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv dbus libpam-systemd openssh-server ca-certificates curl \
        git python3 python3-venv iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash shop \
    && mkdir -p /home/shop/.ssh && chmod 700 /home/shop/.ssh \
    && chown -R shop:shop /home/shop/.ssh \
    && systemctl enable ssh \
    # a container has no consoles or udev to wait for
    && systemctl mask systemd-udevd.service systemd-udev-trigger.service \
        getty.target console-getty.service systemd-firstboot.service

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
