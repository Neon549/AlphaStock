#!/usr/bin/env bash
# One-time, reversible preparation for GitHub Actions deployments.
# Run from an existing root/OrcaTerm session before committing the workflow
# change that starts using the deploy account.

set -euo pipefail

DEPLOY_USER="deploy"
OPS_GROUP="ubuntu"
APP_DIR="/home/ubuntu/Alpha_stock"
VENV_DIR="/home/ubuntu/venv"
FRONTEND_DIST_DIR="/home/ubuntu/frontend/Alpha_stock_frontend/react-app/dist"
SSH_KEYS="/root/.ssh/authorized_keys"
SUDOERS_FILE="/etc/sudoers.d/alphastock-deploy"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root from the server console." >&2
  exit 1
fi

for required_path in "${APP_DIR}" "${VENV_DIR}" "${SSH_KEYS}"; do
  if [[ ! -e "${required_path}" ]]; then
    echo "Required path is missing: ${required_path}" >&2
    exit 1
  fi
done
if ! getent group "${OPS_GROUP}" >/dev/null; then
  echo "Required operating-system group is missing: ${OPS_GROUP}" >&2
  exit 1
fi

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --gid "${OPS_GROUP}" "${DEPLOY_USER}"
elif [[ "$(id -gn "${DEPLOY_USER}")" != "${OPS_GROUP}" ]]; then
  echo "Existing ${DEPLOY_USER} account does not use primary group ${OPS_GROUP}; refusing to alter it." >&2
  exit 1
fi
passwd -l "${DEPLOY_USER}" >/dev/null

allow_users="$(sshd -T 2>/dev/null | awk '$1 == "allowusers" { $1=""; sub(/^ /, ""); print }')"
deny_users="$(sshd -T 2>/dev/null | awk '$1 == "denyusers" { $1=""; sub(/^ /, ""); print }')"
if [[ -n "${allow_users}" && " ${allow_users} " != *" ${DEPLOY_USER} "* ]]; then
  echo "sshd AllowUsers does not permit ${DEPLOY_USER}; update SSH policy before cutover." >&2
  exit 1
fi
if [[ " ${deny_users} " == *" ${DEPLOY_USER} "* ]]; then
  echo "sshd DenyUsers blocks ${DEPLOY_USER}; update SSH policy before cutover." >&2
  exit 1
fi

install -d -m 0700 -o "${DEPLOY_USER}" -g "${OPS_GROUP}" "/home/${DEPLOY_USER}/.ssh"
# Only migrate the existing GitHub Actions keys.  Do not copy a human root key
# into the deployment account.  Deduplicate by public-key material.
awk '$NF == "github-actions" && !seen[$2]++ { print }' "${SSH_KEYS}" \
  > "/home/${DEPLOY_USER}/.ssh/authorized_keys"
if [[ ! -s "/home/${DEPLOY_USER}/.ssh/authorized_keys" ]]; then
  echo "No github-actions public key found in ${SSH_KEYS}; refusing to continue." >&2
  exit 1
fi
chown "${DEPLOY_USER}:${OPS_GROUP}" "/home/${DEPLOY_USER}/.ssh/authorized_keys"
chmod 0600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"

# The service currently lives below /home/ubuntu.  Keep its existing owner but
# make the deployment group writable and setgid so future git/pip artifacts
# remain writable by deploy and readable by the service account.
for writable_path in "${APP_DIR}" "${VENV_DIR}"; do
  chgrp -R "${OPS_GROUP}" "${writable_path}"
  chmod -R g+rwX "${writable_path}"
  find "${writable_path}" -type d -exec chmod g+s {} +
done
if [[ -d "${FRONTEND_DIST_DIR}" ]]; then
  chgrp -R "${OPS_GROUP}" "${FRONTEND_DIST_DIR}"
  chmod -R g+rwX "${FRONTEND_DIST_DIR}"
  find "${FRONTEND_DIST_DIR}" -type d -exec chmod g+s {} +
fi
# deploy needs to traverse the existing application owner's home, but cannot
# list it or write outside the explicitly prepared directories above.
chmod g+rx /home/ubuntu

SYSTEMCTL_BIN="$(command -v systemctl)"
cat > "${SUDOERS_FILE}" <<EOF
# GitHub Actions deployment account: service control only.
${DEPLOY_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} restart alphastock-api.service, ${SYSTEMCTL_BIN} reload nginx.service
EOF
chmod 0440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}"

runuser -u "${DEPLOY_USER}" -- test -w "${APP_DIR}/.git"
runuser -u "${DEPLOY_USER}" -- test -x "${VENV_DIR}/bin/python"
sudo -l -U "${DEPLOY_USER}"

echo "deploy user provisioned successfully."
echo "Verify the next GitHub Actions deployment, then remove the github-actions keys from /root/.ssh/authorized_keys and disable root SSH login in a separate change."
