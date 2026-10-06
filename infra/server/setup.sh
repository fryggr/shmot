#!/usr/bin/env bash
# Первичная настройка чистого сервера Ubuntu 24.04 LTS. Запуск от root один раз:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/<branch>/infra/server/setup.sh | bash
# или скопировать файл и выполнить: bash setup.sh
set -euo pipefail

DEPLOY_USER=${DEPLOY_USER:-deploy}
# Без интерактивных вопросов apt (раскладка клавиатуры, конфликты конфигов — оставляем текущие)
export DEBIAN_FRONTEND=noninteractive
APT_OPTS=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

# Вход по паролю будет отключён — без SSH-ключа root-а можно потерять доступ к серверу
if [ ! -s /root/.ssh/authorized_keys ]; then
  echo "Нет SSH-ключа в /root/.ssh/authorized_keys. Сначала с вашего компьютера: ssh-copy-id root@<IP>" >&2
  exit 1
fi

apt-get update
apt-get "${APT_OPTS[@]}" upgrade
apt-get "${APT_OPTS[@]}" install ca-certificates curl git ufw fail2ban unattended-upgrades

# Docker Engine + compose plugin из официального репозитория Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get "${APT_OPTS[@]}" install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Пользователь для деплоя (вход только по SSH-ключу root-а, если он уже настроен)
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
  usermod -aG docker "$DEPLOY_USER"
  if [ -f /root/.ssh/authorized_keys ]; then
    install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
    install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
  fi
fi

# Файрвол: только SSH и HTTP(S). Docker-порты наружу не публикуются (кроме Caddy).
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

# SSH: только ключи. Файл в sshd_config.d с префиксом 00 читается первым и перекрывает
# PasswordAuthentication yes из 50-cloud-init.conf, который ставят многие провайдеры.
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /etc/ssh/sshd_config.d/00-shmot.conf
sshd -t && (systemctl reload ssh || systemctl reload sshd || true)

# Swap 2 ГБ — сборка Next.js на маленьком сервере
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

dpkg-reconfigure -f noninteractive unattended-upgrades
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /var/backups/fashion

echo "Готово. Дальше: войти как $DEPLOY_USER и выполнить шаги из docs/deploy.md"
