# Plantilla de Scrapers con Chrome + VNC

Repositorio base para ejecutar scrapers en Docker usando Chrome estable, Selenium/undetected-chromedriver y un entorno gráfico virtual con VNC.

## Contenido

- `Dockerfile.scrapers` - Imagen Docker con Chrome, Node.js, Xvfb, VNC y Python.
- `docker-compose.yml` - Servicio `scraper` listo para levantar con Docker Compose.
- `scrapers/requirements.txt` - Dependencias Python necesarias.
- `scrapers/api.py` - Punto de entrada del scraper; reemplaza este archivo con tu propia lógica.
- `scrapers/start.sh` - Script de arranque del contenedor que inicia Xvfb, VNC, Chrome y tu script Python.

## Características

- Chrome estable instalado en la imagen.
- Entorno gráfico virtual (`Xvfb`) para ejecución de navegadores headful.
- Acceso VNC en el puerto `5900`.
- Acceso Chrome DevTools Protocol (`CDP`) proxy en el puerto `9222`.
- Volumen persistente para perfiles de Chrome y datos en `./data`.

## Requisitos

- Docker
- Docker Compose
- Red externa `coolify` creada en Docker (necesaria para el `docker-compose.yml` actual)

> Si no necesitas la red externa, ajusta `docker-compose.yml` para remover `networks` o usar una red local.

## Uso

1. Construir y levantar el servicio:

```bash
docker compose up -d --build
```

2. Ver logs:

```bash
docker compose logs -f scraper
```

3. Detener y eliminar el servicio:

```bash
docker compose down
```

## Puertos expuestos

- `5900` - VNC
- `9222` - Chrome DevTools Protocol (CDP)

## Personalización

- Modifica `scrapers/api.py` con tu lógica de scraping.
- Añade dependencias en `scrapers/requirements.txt`.
- Cambia variables de entorno en `docker-compose.yml` si necesitas otro `VNC_PASSWORD`.

## Variables importantes

- `VNC_PASSWORD` - contraseña del servidor VNC. Se puede configurar en `docker-compose.yml`.

## Estructura recomendada de trabajo

1. Desarrolla el scraper en `scrapers/api.py`.
2. Añade dependencias extra a `scrapers/requirements.txt`.
3. Reconstruye la imagen si agregas nuevas dependencias.

## Notas

- El contenedor mantiene un usuario no root (`chrome`) para ejecutar Chrome.
- El script `start.sh` reinicia procesos previos y crea un proxy `socat` para exponer el CDP.

---

Asegúrate de adaptar el archivo `api.py` a tu caso de uso y de configurar correctamente la red Docker si usas `coolify`.
