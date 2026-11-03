#!/usr/bin/env bash
# Installation-time only: Docker CE + NVIDIA Container Toolkit on existing WSL Ubuntu 22.04.
# Does not update WSL, upgrade Ubuntu, or install a Linux NVIDIA display driver.
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root inside the existing Ubuntu-22.04 WSL distribution." >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify this Linux distribution." >&2
  exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
  echo "This installer is scoped to Ubuntu 22.04; found ${PRETTY_NAME:-unknown}." >&2
  exit 1
fi
if ! nvidia-smi >/dev/null 2>&1; then
  echo "The existing WSL NVIDIA driver mapping is unavailable; stop before installing containers." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg2

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
NVIDIA_CONTAINER_TOOLKIT_VERSION=1.20.1-1
apt-get install -y \
  "nvidia-container-toolkit=${NVIDIA_CONTAINER_TOOLKIT_VERSION}" \
  "nvidia-container-toolkit-base=${NVIDIA_CONTAINER_TOOLKIT_VERSION}" \
  "libnvidia-container-tools=${NVIDIA_CONTAINER_TOOLKIT_VERSION}" \
  "libnvidia-container1=${NVIDIA_CONTAINER_TOOLKIT_VERSION}"

nvidia-ctk runtime configure --runtime=docker
systemctl enable --now docker
systemctl restart docker

printf '\n=== Docker version ===\n'
docker version
printf '\n=== NVIDIA Container Toolkit ===\n'
nvidia-ctk --version
printf '\n=== NVIDIA runtime ===\n'
docker info --format '{{json .Runtimes}}'
printf '\nContainer runtime installation completed. GPU injection still requires a separate validation run.\n'
