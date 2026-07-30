# Despliegue en AWS — CryptoPulse Analytics

Guía paso a paso para dejar el pipeline de datos y el dashboard funcionando en
AWS usando **exclusivamente la capa gratuita (Free Tier)**. Se asume que ya
tienes una cuenta AWS activa.

## Arquitectura de despliegue

```
Hugging Face (dataset histórico)     CoinGecko API (precio en vivo)
        │  una vez / mensual                 │ cada 15 min (systemd timer)
        ▼                                     ▼
   ┌─────────────────────── EC2 t3.micro (Amazon Linux 2023) ───────────────────────┐
   │  data_pipeline/ (ingesta + transformación)   dashboard/ (Streamlit :8501)      │
   │              │                                        ▲                        │
   │              ▼                                        │ Nginx :80 (proxy)      │
   │        S3 (raw/ processed/ live/) ─── lee para servir ┘                        │
   └──────────────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ IAM Role (permisos mínimos, sin credenciales embebidas)
```

**Por qué esta combinación de servicios (justificación por servicio):**

| Servicio | Por qué se eligió | Free Tier |
|---|---|---|
| **EC2 (t3.micro / t2.micro)** | El enunciado pide desplegar "en una instancia"; una sola instancia aloja tanto el cron de ingesta como el servidor del dashboard, evitando la complejidad de gestionar Lambda + colas para un proyecto de curso | 750 h/mes durante 12 meses |
| **S3** | Desacopla el almacenamiento del ciclo de vida de la instancia (si se recrea el EC2, los datos no se pierden), y separa capas raw/processed/live como un mini data lake | 5 GB + 20k GET / 2k PUT al mes |
| **IAM Role para la instancia** | Buenas prácticas: la instancia obtiene permisos S3 sin credenciales de larga duración incrustadas en el código | Sin costo |
| **Security Group** | Firewall a nivel de instancia: solo abre 22 (tu IP) y 80 (dashboard público) | Sin costo |
| **Nginx** | Reverse proxy para exponer Streamlit (que corre en 127.0.0.1:8501) en el puerto 80 estándar, y punto de apoyo si luego se agrega HTTPS | Sin costo (software libre) |

---

## Paso 1 — Crear el bucket S3

1. Consola AWS → **S3** → *Create bucket*.
2. Nombre único global, ej. `cryptopulse-analytics-<tu-alias>` (anótalo, se usa en todos los pasos siguientes).
3. Región: la más cercana (ej. `us-east-1`).
4. Deja *Block all public access* activado (el dashboard no sirve archivos S3 directamente, los lee el backend con permisos IAM).
5. Crear. Dentro, crea (opcional, se crean solos al subir) los "folders" `raw/`, `processed/`, `live/`.

## Paso 2 — Crear el IAM Role para la instancia EC2

1. Consola AWS → **IAM** → *Policies* → *Create policy* → pestaña JSON → pega el contenido de [`iam_policy_s3.json`](iam_policy_s3.json), reemplazando `REEMPLAZA-CON-TU-BUCKET` por el nombre real del bucket.
2. Nombra la política, ej. `CryptoPulseS3Access`.
3. **IAM** → *Roles* → *Create role* → tipo de entidad de confianza: **AWS service → EC2**.
4. Adjunta la política `CryptoPulseS3Access` creada en el paso anterior.
5. Nombra el rol, ej. `CryptoPulseEC2Role`.

## Paso 3 — Lanzar la instancia EC2

1. Consola AWS → **EC2** → *Launch instance*.
2. Nombre: `cryptopulse-dashboard`.
3. AMI: **Amazon Linux 2023** (free tier eligible).
4. Tipo de instancia: **t3.micro** (o `t2.micro` según la región/cuenta, ambas cubiertas por Free Tier).
5. Par de claves: crea uno nuevo (`cryptopulse-key.pem`) y descárgalo — lo necesitas para SSH.
6. Configuración de red → *Edit* Security Group, crea uno nuevo con estas reglas:
   - SSH (22) → **Mi IP** (nunca `0.0.0.0/0` para SSH)
   - HTTP (80) → `0.0.0.0/0` (acceso público al dashboard)
   - (Opcional, si luego agregas HTTPS) HTTPS (443) → `0.0.0.0/0`
7. Almacenamiento: 8–10 GB gp3 (dentro del free tier de 30 GB).
8. **Advanced details → IAM instance profile** → selecciona `CryptoPulseEC2Role`.
9. *Launch instance*.
10. Una vez "running", anota la **IP pública** (o asigna una **Elastic IP** para que no cambie si reinicias la instancia — Elastic IP es gratis mientras esté asociada a una instancia corriendo).

## Paso 4 — Conectarse por SSH

```bash
chmod 400 cryptopulse-key.pem
ssh -i cryptopulse-key.pem ec2-user@<IP-PUBLICA-DE-TU-INSTANCIA>
```

## Paso 5 — Subir el proyecto a la instancia

Opción recomendada: subir el código a un repositorio Git (GitHub) y clonarlo en
la instancia. Alternativa rápida sin Git, desde tu máquina local:

```bash
scp -i cryptopulse-key.pem -r "Proyecto topicos de data science 1" \
    ec2-user@<IP-PUBLICA>:/home/ec2-user/cryptopulse
```

## Paso 6 — Generar los datos procesados **antes** de subirlos (recomendado)

La descarga histórica completa desde Hugging Face (varios millones de filas
a 1 minuto, 2017–2025) es pesada para una instancia de 1 GB de RAM. Por eso
el flujo recomendado es:

1. Ejecutar **una sola vez, desde tu laptop** (donde ya se hizo en esta
   sesión):
   ```bash
   python data_pipeline/ingest_historical.py --upload-s3
   ```
   Esto descarga el histórico, lo resamplea a **diario** (reduce de millones
   de filas a unos pocos miles) y sube `data/processed/*` directo a S3.
2. En la instancia EC2 (liviana, sin RAM para el histórico completo) solo
   corren `ingest_live.py` (feed en vivo de CoinGecko) y `transform.py`
   (recombina histórico + vivo y recalcula KPIs), programados cada 15 min.
3. El dashboard (`DATA_SOURCE=s3`) siempre lee la versión más reciente desde
   S3, sin depender de que la instancia haya descargado nunca el histórico
   crudo.

Si prefieres que **todo** corra en la nube sin pasos manuales locales, puedes
lanzar temporalmente una instancia más grande (ej. `t3.medium`, fuera de free
tier por unas pocas horas) solo para correr `ingest_historical.py --upload-s3`
una vez, y luego volver a `t3.micro` para el resto — o simplemente ejecutar el
histórico una vez desde tu equipo, como se hizo aquí.

## Paso 7 — Bootstrap de la instancia

Dentro de la instancia (por SSH):

```bash
chmod +x deploy/bootstrap_ec2.sh
./deploy/bootstrap_ec2.sh
```

Este script (ver [`bootstrap_ec2.sh`](bootstrap_ec2.sh)):
- instala Python 3.11, Nginx y dependencias del sistema,
- crea el entorno virtual e instala `requirements.txt`,
- registra los servicios `systemd`:
  - **`streamlit.service`** → sirve el dashboard en `127.0.0.1:8501`, siempre activo (`Restart=on-failure`),
  - **`crypto-live.timer`** → dispara `crypto-live.service` cada 15 minutos, que corre `ingest_live.py --upload-s3` y `transform.py --upload-s3`,
- configura **Nginx** como proxy inverso `80 → 8501`.

Antes de correrlo, edita `deploy/streamlit.service` y `deploy/crypto-live.service`
reemplazando `REEMPLAZA-CON-TU-BUCKET` por el nombre real de tu bucket S3.

## Paso 8 — Verificar

```bash
sudo systemctl status streamlit          # active (running)
sudo systemctl status crypto-live.timer  # active (waiting)
sudo systemctl list-timers crypto-live*  # próxima ejecución programada
curl -I http://localhost                 # 200 OK vía Nginx
```

Desde tu navegador: `http://<IP-PUBLICA-DE-TU-INSTANCIA>` — el dashboard
debería cargar.

## Paso 9 (opcional) — Dominio + HTTPS

Si tienes un dominio propio, apunta un registro A a la Elastic IP y luego:

```bash
sudo dnf install -y certbot python3-certbot-nginx
sudo certbot --nginx -d tu-dominio.com
```

Certbot (Let's Encrypt) es gratuito y renueva automáticamente el certificado.

## Operación y costos

- **Apagar la instancia** cuando no se esté usando (ej. fuera del período de
  evaluación) evita agotar las 750 h/mes del Free Tier si tienes otras
  instancias corriendo en paralelo.
- Los logs de cada corrida del feed en vivo se ven con
  `journalctl -u crypto-live.service -f`.
- Para actualizar el código: `git pull` (o `scp` de nuevo) dentro de
  `/home/ec2-user/cryptopulse` y `sudo systemctl restart streamlit`.
- **Evidencia para el trabajo**: capturar pantalla/video de
  `systemctl status`, del dashboard cargando en el navegador con la IP
  pública, y de los objetos `raw/`, `processed/`, `live/` poblados en el
  bucket S3 (consola AWS).
