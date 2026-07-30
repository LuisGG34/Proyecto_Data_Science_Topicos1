#!/usr/bin/env bash
# Bootstrap de la instancia EC2 (Amazon Linux 2023) para CryptoPulse Analytics.
# Ejecutar por SSH dentro de la instancia, una sola vez.
set -euo pipefail

echo ">> Actualizando paquetes e instalando dependencias del sistema..."
sudo dnf update -y
sudo dnf install -y python3.11 python3.11-pip git nginx

echo ">> Clonando el proyecto..."
cd /home/ec2-user
if [ ! -d cryptopulse ]; then
  git clone <URL-DE-TU-REPOSITORIO> cryptopulse
fi
cd cryptopulse

echo ">> Creando entorno virtual e instalando dependencias Python..."
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo ">> Instalando unidades systemd..."
sudo cp deploy/streamlit.service /etc/systemd/system/streamlit.service
sudo cp deploy/crypto-live.service /etc/systemd/system/crypto-live.service
sudo cp deploy/crypto-live.timer /etc/systemd/system/crypto-live.timer
sudo systemctl daemon-reload
sudo systemctl enable --now streamlit.service
sudo systemctl enable --now crypto-live.timer

echo ">> Configurando Nginx como proxy inverso..."
sudo cp deploy/nginx_cryptopulse.conf /etc/nginx/conf.d/cryptopulse.conf
sudo systemctl enable --now nginx
sudo systemctl reload nginx

echo ">> Listo. Verifica el estado con:"
echo "   sudo systemctl status streamlit"
echo "   sudo systemctl status crypto-live.timer"
echo "   curl -I http://localhost"
