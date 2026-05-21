# SDN GÉANT Dashboard

> **Demo y tutoriales:**
> - [Tutorial de uso](https://www.youtube.com/watch?v=ENLACE_TUTORIAL) — guía paso a paso 
> - [Vista en dispositivo móvil](https://www.youtube.com/watch?v=ENLACE_MOVIL) — demostración del dashboard responsivo en teléfono

---

Sistema de monitoreo y control en tiempo real para redes definidas por software (SDN), construido sobre la topología de la red académica europea GÉANT. Integra una red virtual emulada en Mininet, un controlador SDN Ryu con clasificación de tráfico por machine learning, una API Flask que expone métricas en tiempo real, y un panel web responsivo accesible desde cualquier dispositivo en la red local.

La topología replica 23 switches interconectados con una densidad de enlaces característica de redes de tránsito de alta capacidad. Cada switch tiene asociado un host, y el sistema calcula rutas óptimas mediante el algoritmo de Dijkstra cuyos pesos de enlace se actualizan dinámicamente según el tipo de tráfico clasificado por un modelo RandomForest (VIDEO, HTTP, GAMING, ICMP).

```

```

---

## Tabla de Contenidos

1. [Inventario de Archivos](#1-inventario-de-archivos)
2. [Requisitos de las Máquinas Virtuales](#2-requisitos-de-las-máquinas-virtuales)
3. [Configuración de Red entre VMs](#3-configuración-de-red-entre-vms)
4. [Puesta en Marcha](#4-puesta-en-marcha)
5. [Generación del Dataset y Entrenamiento del Modelo ML](#5-generación-del-dataset-y-entrenamiento-del-modelo-ml)
6. [Dashboard Web — Componentes y Visualizaciones](#6-dashboard-web--componentes-y-visualizaciones)
7. [Dependencias Externas del Frontend](#7-dependencias-externas-del-frontend)
8. [Endpoints de la API REST](#8-endpoints-de-la-api-rest)
9. [Resumen de Puertos y Servicios](#9-resumen-de-puertos-y-servicios)
10. [Solución de Problemas Frecuentes](#10-solución-de-problemas-frecuentes)

---

## 1. Inventario de Archivos

### Archivos del sistema

| Archivo | Descripción |
|---|---|
| `icmpgeant.py` | Lanza la topología GÉANT en Mininet con generación de tráfico ICMP (ping continuo entre hosts). 23 switches OVS, 23 hosts (h1–h23) y malla de enlaces entre switches. Conecta con el controlador remoto vía OpenFlow 1.3. |
| `httpgeant.py` | Variante de la topología con generación de tráfico HTTP (descargas masivas con wget). Misma estructura de red que `icmpgeant.py`, perfil de tráfico orientado a ráfagas de alto throughput. |
| `rtpgeant.py` | Variante con generación de tráfico de vídeo/RTP (streams UDP con iperf3). Perfil de tráfico de caudal continuo y moderado, representativo de videoconferencia o streaming. |
| `gaminggeant.py` | Variante con generación de tráfico tipo gaming (UDP low-latency, paquetes pequeños y frecuentes). Perfil de bajo throughput y alta tasa de paquetes. |
| `geant_controller.py` | Controlador Ryu unificado. Gestiona eventos OpenFlow (PacketIn, PortStats, SwitchEnter/Leave), construye el grafo NetworkX, ejecuta Dijkstra con pesos dinámicos, carga el modelo ML y expone todos los endpoints REST mediante Flask. |
| `server.js` | Backend Node.js/Express que actúa como proxy inverso entre el dashboard web y la API Flask del controlador. Sirve los archivos estáticos del frontend, expone los endpoints `/api/*` al navegador y detecta automáticamente las IPs locales de la máquina. |
| `index.html` | Estructura HTML del dashboard: cinco pestañas (Topología, Métricas, ML, Avanzado, Control), barra de estado del controlador y pie de página con hora actualizada en tiempo real. Importa Chart.js y D3.js desde CDN. |
| `script.js` | Lógica frontend completa: visualización de topología con D3.js (simulación de fuerzas), más de doce gráficas Chart.js (throughput, utilización, RX/TX, error rates, radar ML, etc.), polling automático de todos los endpoints, gestión de rutas Dijkstra y control de hosts (bloquear/permitir). |
| `style.css` | Hoja de estilos del dashboard: tema oscuro con paleta azul GÉANT, diseño responsivo compatible con móviles y tablets, estilos de tablas, badges de estado, chips de URL de acceso y animaciones de barra de estado. |
| `build_dataset.py` | Preprocesamiento del dataset. Une los CSV de captura por clase, aplica limpieza de contadores desbordados, filtra tráfico de control OpenFlow, elimina artefactos de asimetría de puertos, balancea las clases al mínimo común y exporta `dataset_balanced.csv`. |
| `entrenamiento.py` | Entrena el clasificador RandomForest sobre `dataset_balanced.csv` con 200 árboles, validación cruzada estratificada de 5 folds y análisis de importancia de features. Exporta los tres artefactos necesarios para el controlador. |

### Artefactos del Modelo ML

Estos archivos son generados por `entrenamiento.py` y deben estar presentes en el mismo directorio que `geant_controller.py` antes de arrancar el controlador.

| Artefacto | Descripción |
|---|---|
| `TrafficModel.pkl` | Modelo RandomForest serializado (200 árboles, `max_depth=10`). Clasifica el tráfico de cada puerto en VIDEO, HTTP, GAMING o ICMP a partir de 11 métricas por ventana de muestreo. |
| `LabelEncoder.pkl` | Codificador scikit-learn que convierte las predicciones numéricas del modelo a etiquetas de texto (VIDEO, HTTP, GAMING, ICMP). |
| `feature_columns.pkl` | Lista de las 11 columnas de features en el orden exacto que espera el modelo. Garantiza la alineación entre el vector generado por el controlador en producción y el vector con el que fue entrenado. |

---

## 2. Requisitos de las Máquinas Virtuales

### VM 1 — Mininet

Ejecuta la red virtual emulada. Se recomienda Ubuntu Server 20.04 LTS o 22.04 LTS.

| Recurso | Mínimo recomendado |
|---|---|
| CPU | 2 vCPUs (x86-64) |
| RAM | 2 GB |
| Disco | 10 GB |
| Red | Adaptador en modo Bridged o Host-Only compartido con VM 2 |
| Sistema operativo | Ubuntu 20.04 LTS / 22.04 LTS (64 bit) |
| Kernel | Módulo `openvswitch-datapath` disponible |

**Instalación de dependencias:**

```bash
sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch python3 python3-pip

# Verificar que OVS está activo
sudo systemctl start openvswitch-switch
sudo ovs-vsctl show

# Herramientas de generación de tráfico (recomendadas)
sudo apt-get install -y iperf3 wget curl
```

---

### VM 2 — Ryu SDN Controller

Ejecuta el controlador OpenFlow y la API Flask. Requiere Python 3.8 o superior y acceso de red a la VM 1.

| Recurso | Mínimo recomendado |
|---|---|
| CPU | 2 vCPUs (x86-64) |
| RAM | 2 GB (4 GB recomendado con el modelo ML cargado) |
| Disco | 10 GB |
| Red | Adaptador en modo Bridged o Host-Only compartido con VM 1 |
| Sistema operativo | Ubuntu 20.04 LTS / 22.04 LTS (64 bit) |
| Python | 3.8 o superior |

**Instalación de dependencias:**

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-dev build-essential

# Ryu SDN Framework
pip3 install ryu

# API y comunicación
pip3 install flask flask-cors

# Grafo y enrutamiento
pip3 install networkx

# Machine Learning
pip3 install scikit-learn joblib pandas numpy

# Verificar instalación
ryu-manager --version
```

---

## 3. Configuración de Red entre VMs

Ambas VMs deben poder comunicarse entre sí. Se recomienda configurarlas en el mismo segmento de red (modo **Bridged** en el hipervisor, o una red **Host-Only** compartida).

Antes de arrancar el sistema, identifica la IP de cada VM y configúrala en los archivos correspondientes:

- **VM 2 (Ryu controller):** anota su IP y colócala como valor de `CONTROLLER_IP` en cada script de topología (VM 1), y como valor de `RYU_FLASK` / `RYU_OPENFLOW` en `server.js`.
- **VM 1 (Mininet):** no requiere configuración de IP fija; solo necesita alcanzar a la VM 2.

```
VM 2 (Ryu controller):  <IP de la VM 2>
  → Puerto OpenFlow:    TCP 6633
  → Puerto Flask API:   TCP 8888

VM 1 (Mininet):         <IP de la VM 1>
```

> **Nota:** Si prefieres no editar los archivos fuente, puedes pasar las IPs como variables de entorno al lanzar el servidor Node.js:
> ```bash
> RYU_FLASK=http://<IP-VM2>:8888 RYU_IP=http://<IP-VM2>:8080 node server.js
> ```

Verifica la conectividad antes de continuar:

```bash
# Desde VM 1, verificar que llega al controlador
ping -c 3 <IP-VM2>

# Abrir los puertos necesarios en VM 2 si hay firewall activo
sudo ufw allow 6633/tcp
sudo ufw allow 8888/tcp
```

---

## 4. Puesta en Marcha

El sistema debe arrancarse siempre en este orden: primero el controlador Ryu, luego el script de topología Mininet correspondiente al tipo de tráfico deseado y, por último, el backend Node.js del dashboard.

### Paso 1 — Iniciar el controlador Ryu (VM 2)

Asegúrate de que los tres artefactos ML (`TrafficModel.pkl`, `LabelEncoder.pkl`, `feature_columns.pkl`) estén en el mismo directorio que `geant_controller.py`.

```bash
# Desde el directorio que contiene geant_controller.py
ryu-manager geant_controller.py \
    ryu.app.ofctl_rest \
    ryu.topology.switches \
    --observe-links \
    --ofp-tcp-listen-port 6633
```

El controlador levanta automáticamente la API Flask en el puerto 8888. Deberías ver en la consola:

```
Flask API running on 0.0.0.0:8888
```

> **Advertencia crítica:** Los módulos `ryu.app.ofctl_rest` y `ryu.topology.switches`, junto con la opción `--observe-links`, son **obligatorios**. Sin ellos, el grafo interno del controlador tendrá siempre 0 edges y el algoritmo de Dijkstra fallará con error 503.

---

### Paso 2 — Lanzar la topología Mininet (VM 1)

Elige el script según el tipo de tráfico que deseas generar y capturar:

| Script | Tipo de tráfico | Descripción |
|---|---|---|
| `icmpgeant.py` | ICMP | Ping continuo entre hosts. Tráfico de control de baja intensidad. |
| `httpgeant.py` | HTTP | Descargas masivas con wget. Ráfagas de alto throughput. |
| `rtpgeant.py` | VIDEO/RTP | Streams UDP con iperf3. Caudal continuo y moderado. |
| `gaminggeant.py` | GAMING | UDP low-latency. Paquetes pequeños y alta frecuencia. |

```bash
# Requiere privilegios de root — reemplaza con el script deseado
sudo python3 icmpgeant.py
# sudo python3 httpgeant.py
# sudo python3 rtpgeant.py
# sudo python3 gaminggeant.py
```

Espera hasta ver el prompt de la CLI de Mininet:

```
mininet>
```

Desde la CLI, lanza un `pingall` para forzar el descubrimiento LLDP y poblar el grafo del controlador:

```
mininet> pingall
```

El controlador recibirá los eventos de topología y construirá el grafo en los siguientes 15–30 segundos.

---

### Paso 3 — Iniciar el backend Node.js (máquina del dashboard)

El backend puede correr en la VM 2, en la VM 1 o en una tercera máquina, siempre que tenga acceso HTTP a la VM 2 en el puerto 8888.

```bash
# Instalar Node.js 18.x si no está disponible
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs

# En el directorio que contiene server.js, index.html, script.js y style.css
npm install express cors axios

# Iniciar el servidor
node server.js
```

Salida esperada:

```
✔ Backend corriendo en http://localhost:3000
✔ Proxying Ryu Flask API en http://<IP-VM2>:8888
```

El dashboard estará disponible en `http://<IP-de-la-máquina>:3000` desde cualquier dispositivo en la misma red local. La propia interfaz muestra en la pestaña **Control** las URLs de acceso detectadas automáticamente.

---

## 5. Generación del Dataset y Entrenamiento del Modelo ML

Los artefactos ML pueden estar precompilados y listos para usar. Esta sección describe cómo regenerarlos si se capturan nuevos datos de tráfico.

### Archivos de entrada requeridos

`build_dataset.py` espera cuatro archivos CSV en el mismo directorio, uno por cada clase de tráfico generada con los scripts de topología:

| Archivo CSV | Script que lo genera | Clase |
|---|---|---|
| `icmp_metrics.csv` | `icmpgeant.py` | ICMP |
| `http_metrics.csv` | `httpgeant.py` | HTTP |
| `video_metrics.csv` | `rtpgeant.py` | VIDEO |
| `gaming_metrics.csv` | `gaminggeant.py` | GAMING |

### Preprocesar y balancear el dataset

```bash
# VM 2 — requiere pandas, numpy
python3 build_dataset.py
# Genera: dataset_balanced.csv
```

El script balancea automáticamente las clases al mínimo común, filtra tráfico de control OpenFlow, elimina artefactos de desbordamiento de contadores OVS y aplica un cap de MTU (1500 B) a `bytes_per_packet`.

### Entrenar el modelo

```bash
# VM 2 — requiere scikit-learn, joblib, pandas, numpy, matplotlib
python3 entrenamiento.py
```

Genera los tres artefactos para el controlador (`TrafficModel.pkl`, `LabelEncoder.pkl`, `feature_columns.pkl`) y la figura `feature_importance.png` con el análisis de importancia de variables.

### Variables utilizadas para el entrenamiento

El modelo clasifica el tráfico a partir de **11 features** extraídas de las estadísticas de puerto OpenFlow en ventanas de muestreo. Estas variables capturan distintas dimensiones del comportamiento de la red:

| Feature | Descripción |
|---|---|
| `throughput_mbps` | Caudal de transmisión TX en Mbps. Principal discriminador entre clases de alto (HTTP) y bajo (ICMP) volumen. |
| `rx_throughput_mbps` | Caudal de recepción RX en Mbps. Complementa al TX para detectar flujos asimétricos. |
| `utilization` | Porcentaje de utilización del enlace respecto al ancho de banda nominal. |
| `port_asymmetry` | Diferencia porcentual entre TX y RX. Alta en tráfico unidireccional (streaming), baja en tráfico bidireccional (gaming). |
| `bytes_per_packet` | Tamaño medio de paquete en bytes. HTTP y VIDEO usan paquetes grandes (≈1400 B); ICMP y GAMING usan paquetes pequeños (64–256 B). |
| `packet_rate` | Número de paquetes por segundo. Distingue GAMING (alta frecuencia, paquetes pequeños) de VIDEO (frecuencia media, paquetes grandes). |
| `jitter_mbps` | Variación del throughput entre ventanas consecutivas. Alto en HTTP (ráfagas), bajo en VIDEO (streaming constante). |
| `burstiness` | Coeficiente de variación del throughput. Cuantifica la irregularidad temporal del flujo. |
| `rx_tx_ratio` | Cociente RX/TX. Permite identificar si el nodo es predominantemente emisor o receptor. |
| `tx_error_rate` | Tasa de errores en transmisión respecto al total de paquetes TX. |
| `rx_error_rate` | Tasa de errores en recepción respecto al total de paquetes RX. |

> La figura `feature_importance.png` generada por `entrenamiento.py` muestra la contribución relativa de cada variable al modelo. Coloca aquí la imagen para referencia visual:

<!-- IMAGEN: feature_importance.png generada por entrenamiento.py -->
![Importancia de variables del modelo RandomForest](feature_importance.png)

**Clases:** `VIDEO` · `HTTP` · `GAMING` · `ICMP`

---

## 6. Dashboard Web — Componentes y Visualizaciones

El panel web es totalmente responsivo y se adapta a escritorio, tablet y móvil. Consta de cinco pestañas principales.

### Pestaña 1 — Topología

Muestra el grafo de la red GÉANT en tiempo real mediante una simulación de fuerzas D3.js. Los nodos representan switches y las aristas los enlaces físicos entre ellos.

- Zoom y paneo interactivo (rueda del ratón o gestos táctiles). Doble clic para centrar la vista automáticamente.
- Los nodos del camino activo calculado por Dijkstra se resaltan en color dorado.
- Selector de switch origen/destino con botón de cálculo de ruta óptima.
- Tabla de rutas precalculadas con los caminos más frecuentes.

<!-- IMAGEN: captura de pantalla de la pestaña Topología -->
![Pestaña Topología](screenshots/tab_topology.png)

---

### Pestaña 2 — Métricas

Panel de monitoreo de rendimiento en tiempo real.

- Tabla completa por switch/puerto: throughput TX/RX, utilización, bytes/paquete, packet rate, jitter, burstiness, ratio RX/TX, error rates, packet loss y drop rate.
- Indicadores visuales `idle_flag`: puntos de color que indican si el puerto tiene tráfico activo o está en reposo.
- Gráfica de throughput histórico con una línea por switch (ventana de 40 ciclos).
- Gráfica de historial de packet loss y de utilización media de la red.
- Gauge strip SVG de utilización por switch con color semafórico (verde → amarillo → rojo).

<!-- IMAGEN: captura de pantalla de la pestaña Métricas -->
![Pestaña Métricas](screenshots/tab_metrics.png)

---

### Pestaña 3 — Predicciones ML

Visualizaciones del clasificador de tráfico en tiempo real.

- Gráfica de pastel con la distribución actual de tipos de tráfico detectados.
- Gráfica de barras con el packet rate agrupado por tipo de tráfico predicho.
- Radar chart con el perfil multidimensional de cada clase (throughput, jitter, burstiness, utilización, packet rate).
- Tabla de predicciones activas: tipo, switch, puerto, peso de enlace y throughput.

<!-- IMAGEN: captura de pantalla de la pestaña Predicciones ML -->
![Pestaña Predicciones ML](screenshots/tab_ml.png)

---

### Pestaña 4 — Avanzado

Métricas detalladas para análisis profundo.

- Gráfica de barras apiladas RX vs TX por switch.
- Gráfica de barras horizontales de bytes/paquete por switch.
- Scatter chart de ratio RX/TX por puerto.
- Gráfica de barras agrupadas de packet loss y drop rate.
- Historial de PPS (paquetes por segundo) por ciclo.
- Historial de burstiness (coeficiente de variación del throughput).
- Gráfica de área de bytes/segundo histórico.
- Error rate bar chart (TX y RX) por switch.

<!-- IMAGEN: captura de pantalla de la pestaña Avanzado -->
![Pestaña Avanzado](screenshots/tab_advanced.png)

---

### Pestaña 5 — Control de Red

Panel de administración de hosts y políticas QoS.

- Tabla dinámica de hosts detectados por el controlador con IP, MAC, switch, puerto y estado (permitido/bloqueado).
- Botones por fila para bloquear o permitir el tráfico de un host individualmente mediante reglas OpenFlow.
- Filtro de búsqueda en tiempo real sobre IP, MAC o número de switch.
- Distinción visual entre hosts descubiertos dinámicamente (L2) y hosts estáticos de la topología.
- Editor de políticas QoS por clase de tráfico: `throughput_factor`, `jitter_factor` y `burst_factor`.
- Toggle global para activar o desactivar el enrutamiento QoS basado en ML.
- Panel de información de red con la IP del servidor, puerto y URLs de acceso para otros dispositivos en la LAN, con botón de copia al portapapeles.

<!-- IMAGEN: captura de pantalla de la pestaña Control de Red -->
![Pestaña Control de Red](screenshots/tab_control.png)

---

## 7. Dependencias Externas del Frontend

El dashboard carga dos bibliotecas desde CDN. Se requiere conexión a Internet la primera vez, o alojar los archivos localmente para entornos sin salida a Internet.

| Biblioteca | Versión | Uso |
|---|---|---|
| [Chart.js](https://www.chartjs.org/) | 4.x | Todas las gráficas de métricas, ML e historial (líneas, barras, pastel, radar, área) |
| [D3.js](https://d3js.org/) | 7.x | Visualización de topología con simulación de fuerzas, zoom y paneo interactivo |

Para entornos sin acceso a Internet, descarga los archivos y actualiza las rutas en `index.html`:

```bash
wget https://cdn.jsdelivr.net/npm/chart.js/dist/chart.umd.min.js -O chart.min.js
wget https://d3js.org/d3.v7.min.js -O d3.v7.min.js
```

```html
<!-- Reemplazar en index.html -->
<script src="/chart.min.js"></script>
<script src="/d3.v7.min.js"></script>
```

---

## 8. Endpoints de la API REST

El backend Node.js (puerto 3000) actúa como proxy hacia la API Flask del controlador (puerto 8888). Todos los endpoints son accesibles desde el navegador sin configuración adicional de CORS.

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/status` | Estado del controlador: número de switches, enlaces y timestamp de última actualización |
| GET | `/api/topology` | Grafo completo: lista de nodos y edges con campos `src/dst` y `source/target` (compatibles con D3) |
| GET | `/api/metrics` | Métricas actuales de todos los puertos de todos los switches |
| GET | `/api/metrics/:dpid` | Métricas filtradas por switch (datapath ID) |
| GET | `/api/predictions` | Predicciones ML activas: tipo de tráfico y peso por switch/puerto |
| GET | `/api/routes` | Rutas Dijkstra precalculadas |
| GET | `/api/route?src=N&dst=M` | Calcular ruta óptima entre dos switches |
| GET | `/api/hosts` | Lista de hosts detectados dinámicamente por el controlador |
| POST | `/api/host/block` | Instalar regla OpenFlow para bloquear un host por IP |
| POST | `/api/host/allow` | Eliminar regla de bloqueo y permitir tráfico del host |
| GET | `/api/policy` | Leer políticas QoS actuales por clase de tráfico |
| POST | `/api/policy` | Actualizar políticas QoS (factores de peso por clase) |
| GET | `/api/debug` | Diagnóstico del grafo interno: nodos, edges y estado de conectividad |
| GET | `/api/snapshot` | Snapshot completo del estado interno del controlador |
| GET | `/api/network-info` | IPs locales detectadas del servidor Node.js |

---

## 9. Resumen de Puertos y Servicios

| Puerto | Protocolo | Servicio | Máquina |
|---|---|---|---|
| 6633 | TCP | OpenFlow 1.3 (Ryu controller) | VM 2 |
| 8080 | TCP | Ryu OpenFlow REST API (`ofctl_rest`) | VM 2 |
| 8888 | TCP | Flask API REST del controlador GÉANT | VM 2 |
| 3000 | TCP | Node.js dashboard backend (proxy + frontend estático) | Máquina del dashboard |

---

## 10. Solución de Problemas Frecuentes

### La topología no muestra enlaces (0 edges)

La causa más común es haber arrancado el controlador sin `ryu.topology.switches` o sin `--observe-links`. Verifica el estado con el endpoint de diagnóstico:

```bash
curl http://<IP-VM2>:8888/api/debug
```

Si `edges` es 0, reinicia el controlador con el comando completo indicado en el Paso 1 y lanza un `pingall` desde la CLI de Mininet para disparar el descubrimiento LLDP.

### Dijkstra devuelve error 503

Es consecuencia directa de tener 0 edges en el grafo. Resolver el punto anterior es suficiente.

### El controlador no conecta con Mininet

- Verificar que `CONTROLLER_IP` en los scripts de topología coincide con la IP real de la VM 2.
- Asegurarse de que el puerto TCP 6633 está abierto: `sudo ufw allow 6633/tcp`.
- Confirmar que OVS está en ejecución en la VM 1: `sudo ovs-vsctl show`.

### El modelo ML no carga al arrancar el controlador

El controlador busca los tres artefactos en el directorio de trabajo actual. Ejecuta `ryu-manager` desde el directorio donde se encuentran `TrafficModel.pkl`, `LabelEncoder.pkl` y `feature_columns.pkl`, o bien regenera los artefactos ejecutando `entrenamiento.py`.

### El dashboard no carga datos (controlador aparece como desconectado)

- Verificar que el servidor Node.js alcanza la API Flask: `curl http://<IP-VM2>:8888/api/status`.
- Si las IPs no están configuradas en `server.js`, pasarlas como variables de entorno al lanzar: `RYU_FLASK=http://<IP-VM2>:8888 node server.js`.
- Revisar que el puerto 8888 no está bloqueado por firewall en la VM 2.

---

>  **Demo y tutoriales:**
> - [Tutorial de uso](https://www.youtube.com/watch?v=ENLACE_TUTORIAL)
> - [Vista en dispositivo móvil](https://www.youtube.com/watch?v=ENLACE_MOVIL)
