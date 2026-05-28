/**
 * @file script.js
 * @brief Frontend del Dashboard SDN GÉANT (Versión 3 — Final).
 * @details Gestiona la interfaz gráfica web interactiva en tiempo real utilizando 
 * D3.js para la topología dinámica y Chart.js para las analíticas temporales.
 * * Historial de Correcciones Críticas Aplicadas en v3:
 * - **[D3 TOPOLOGY]**: Corrección del mapeo de enlaces (`source`/`target`) provistos por la API, 
 * previniendo la pérdida de IDs originales de texto que el motor de simulación de fuerza de D3 
 * destruía tras el primer ciclo (*tick*). Normalización de llaves min-max anti-duplicados.
 * - **[CONTROL DE RED DINÁMICO]**: Eliminación de hosts estáticos (*hardcodeados*). Construcción de 
 * tabla interactiva vía `/api/hosts` con descubrimiento por conmutación L2 o método alternativo (*fallback*) 
 * automático para hosts `h1-h23` bajo direccionamiento `10.0.N.1`.
 * - **[SEGURIDAD]**: Interacción con OpenFlow mediante endpoints `/api/host/block` y `/api/host/allow` 
 * para inyectar o remover reglas de descarte de tráfico (*drop rules*) con prioridad 500.
 * - **[MÉTRICAS]**: Supresión de inconsistencias por valores nulos (`bytes=0`) durante la inicialización de puertos.
 */

// 1. CONFIGURACIÓN DE ENDPOINTS Y TIEMPOS DE POLLING

/**
 * @constant {string} API
 * @description Ruta base del servidor proxy de backend o API Gateway de Node.js.
 */
const API = "http://localhost:3000";

/** @constant {number} POLL_STATUS Frecuencia en milisegundos para consultar salud del controlador (8s) */
const POLL_STATUS  = 8000;
/** @constant {number} POLL_METRICS Frecuencia de muestreo para las métricas de throughput de puertos (15s) */
const POLL_METRICS = 15000;
/** @constant {number} POLL_ML Intervalo de actualización para las inferencias de Machine Learning (15s) */
const POLL_ML      = 15000;
/** @constant {number} POLL_TOPO Intervalo de refresco estructural de la topología D3 (20s) */
const POLL_TOPO    = 20000;
/** @constant {number} POLL_ROUTES Intervalo de polling para recuperar las rutas activas de Dijkstra (30s) */
const POLL_ROUTES  = 30000;
/** @constant {number} POLL_HOSTS Frecuencia de polling para descubrir y auditar hosts en la red (20s) */
const POLL_HOSTS   = 20000;

// HISTORIAL DE MÉTRICAS (VENTANAS TEMPORALES)

/**
 * @constant {number} MAX_HISTORY
 * @description Capacidad máxima de muestras consecutivas permitidas para el histórico de gráficos lineales.
 */
const MAX_HISTORY   = 40;

/** @var {Array<string>} historyLabels Almacena las marcas de tiempo (timestamps) de las últimas 40 muestras */
const historyLabels = Array(MAX_HISTORY).fill("");

/** @var {Object} historyDatasets Repositorio indexado por ID de puerto para guardar arreglos de rendimiento lineal */
const historyDatasets = {};

// INSTANCIAS DE GRÁFICOS (CHART.JS)

/** @var {Object|null} bwChartInst Gráfico de barras de Throughput actual */
let bwChartInst      = null;
/** @var {Object|null} lossChartInst Gráfico de barras de pérdidas de paquetes actuales */
let lossChartInst    = null;
/** @var {Object|null} historyChartInst Gráfico lineal histórico de Throughput */
let historyChartInst = null;
/** @var {Object|null} historyLossChartInst Gráfico lineal histórico de tasa de pérdidas */
let historyLossChartInst = null;
/** @var {Object|null} historyUtilChartInst Gráfico lineal histórico del porcentaje de utilización de canales */
let historyUtilChartInst = null;
/** @var {Object|null} pieChartInst Gráfico de dona para la distribución porcentual de tráfico ML */
let pieChartInst     = null;
/** @var {Object|null} barChartInst Gráfico de barras de conteo absoluto por clasificación ML */
let barChartInst     = null;

// ESTADO DE ENRUTAMIENTO Y TOPOLOGÍA (D3.JS)

/** @var {Array<string>} activeRoutePath Traza secuencial de nodos (IDs) que componen la ruta activa seleccionada */
let activeRoutePath = [];

/** @var {Object|null} d3Sim Instancia del motor de simulación de fuerzas físicas de D3.js */
let d3Sim       = null;
/** @var {Array<Object>} d3Nodes Matriz en memoria de switches de red procesados por D3 */
let d3Nodes     = [];
/** @var {Array<Object>} d3Links Matriz en memoria de enlaces inter-switch procesados por D3 */
let d3Links     = [];
/** @var {Object|null} svgRef Elemento del DOM que referencia al contenedor SVG principal */
let svgRef      = null;
/** @var {Object|null} zoomBehavior Objeto de configuración asignado a las interacciones de Zoom y Paneo */
let zoomBehavior = null;
/** @var {boolean} topoInited Bandera de control para evitar la re-inicialización destructiva del lienzo D3 */
let topoInited  = false;

// ESTADO DEL CONTROL DE SEGURIDAD (HOSTS)

/** @var {Array<Object>} hostsData Almacenamiento local estructurado del inventario de hosts activos */
let hostsData = [];
/** @var {string} hostFilter Query string ingresado por el operador para filtrar la tabla de hosts */
let hostFilter = "";

// 2. TAB NAVIGATION (GESTIÓN DE VISTAS DE LA UI)

/**
 * @description Manejador de eventos para la navegación por pestañas en la interfaz de usuario.
 * @details Escucha los clics en todos los elementos con la clase `.tab-btn` para conmutar visualmente 
 * los paneles activos (`.tab-panel`). Resuelve problemas comunes de renderizado asíncrono forzando el 
 * recentrado de la topología D3 o disparando el redimensionamiento mecánico (`.resize()`) de los canvas de Chart.js 
 * para prevenir que aparezcan colapsados o con dimensiones incorrectas al cambiar de pestaña.
 * * Acciones específicas por pestaña:
 * - **topology**: Lanza un temporizador de desfase (`setTimeout`) para recentrar el grafo SVG de D3 de forma limpia.
 * - **metrics**: Fuerza el recalculo dimensional de los gráficos de rendimiento histórico (Throughput, Pérdida y Utilización).
 * - **ml**: Redimensiona el gráfico de distribución por clasificación ML.
 * - **control**: Ejecuta un disparo inmediato del polling de hosts para refrescar el inventario de seguridad OpenFlow.
 */
document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        const tab = btn.dataset.tab;
        
        // Remover clases activas de los botones y paneles previos
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
        
        // Activar el botón seleccionado y su respectivo panel contenedor
        btn.classList.add("active");
        document.getElementById(`tab-${tab}`).classList.add("active");

        // Ajustes mecánicos de renderizado reactivo según la sección seleccionada
        if (tab === "topology" && topoInited) {
            setTimeout(centerTopology, 100);
        }
        if (tab === "metrics"  && historyChartInst) {
            historyChartInst.resize();
            if (historyLossChartInst) historyLossChartInst.resize();
            if (historyUtilChartInst) historyUtilChartInst.resize();
        }
        if (tab === "ml"       && pieChartInst)     pieChartInst.resize();
        if (tab === "control") pollHosts();
    });
});

// 3. API HELPERS (FUNCIONES AUXILIARES REST)

/**
 * @async
 * @function apiFetch
 * @description Abstracción base para realizar solicitudes HTTP GET hacia el backend de Node.js de forma asíncrona.
 * @param {string} endpoint - Ruta relativa del recurso dentro de la API (ej: '/api/status').
 * @returns {Promise<any>} Promesa que resuelve al objeto de datos mapeado directamente desde formato JSON.
 * @throws {Error} Lanza una excepción con el código de estado si la respuesta de red no es exitosa (fuera del rango 200-299).
 */
async function apiFetch(endpoint) {
    const res = await fetch(`${API}${endpoint}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

/**
 * @async
 * @function apiPost
 * @description Abstracción base para enviar datos serializados por JSON mediante solicitudes HTTP POST hacia el backend.
 * @param {string} endpoint - Ruta relativa del endpoint de destino (ej: '/api/host/block').
 * @param {Object} body - Objeto nativo de JavaScript que representa el payload de datos que se enviará en la petición.
 * @returns {Promise<any>} Promesa que resuelve a la respuesta JSON devuelta por el servidor de backend.
 * @throws {Error} Lanza una excepción con el código de estado si el servidor responde con un código de error HTTP.
 */
async function apiPost(endpoint, body) {
    const res = await fetch(`${API}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

// 4. STATUS BAR (MONITOREO DE SALUD DEL CONTROLADOR)
/**
 * @async
 * @function pollStatus
 * @description Realiza una consulta asíncrona hacia el backend para verificar la salud operativa del controlador SDN Ryu.
 * @details Modifica reactivamente los elementos del DOM en el encabezado (*Header*) y pie de página (*Footer*). 
 * Convierte el *timestamp* UNIX devuelto por el controlador (`data.last_update`) a un formato de hora legible local.
 * * * Manejo de Estados de Conectividad en la UI:
 * - **Éxito (Bloque try)**: Cambia el indicador visual (`#statusDot`) a la clase `.online` (color verde), 
 * actualiza los contadores estructurales de la topología física (número de switches y enlaces descubiertos) 
 * y despliega el instante preciso del último escaneo.
 * - **Fallo (Bloque catch)**: Conmuta el indicador visual a la clase `.offline` (color rojo) y altera 
 * el texto a "Controlador desconectado" para alertar al operador en caso de una caída del backend o de Ryu.
 */
async function pollStatus() {
    try {
        const data = await apiFetch("/api/status");
        
        // Actualización de componentes visuales en estado operacional
        document.getElementById("statusDot").className      = "status-dot online";
        document.getElementById("statusText").textContent   = "Controlador en línea";
        document.getElementById("statusSwitches").textContent = `${data.switches} switches`;
        document.getElementById("statusLinks").textContent    = `${data.links} enlaces`;
        
        // Conversión y formateo del timestamp UNIX de la última recolección
        const t = data.last_update
            ? new Date(data.last_update * 1000).toLocaleTimeString()
            : "—";
        document.getElementById("statusTime").textContent = `Actualizado: ${t}`;
    } catch {
        // Fallback visual inmediato en caso de desconexión o timeout HTTP
        document.getElementById("statusDot").className      = "status-dot offline";
        document.getElementById("statusText").textContent   = "Controlador desconectado";
    }
    
    // Sincronización del reloj interno en el footer con la hora del navegador del cliente
    document.getElementById("footerTime").textContent = new Date().toLocaleTimeString();
}

// 5. D3 TOPOLOGY (GRAFO DE FUERZA INTERACTIVO)
/**
 * @function getNodeId
 * @description Resuelve de forma segura el identificador (ID) de un nodo en D3.js.
 * @details **Diagnóstico de Bug Crítico Solucionado**: Durante la inicialización, la colección de enlaces 
 * contiene referencias numéricas o de texto en `source` y `target`. Tras el primer ciclo (*tick*) del motor de fuerzas, 
 * D3 transmuta de forma destructiva dichos campos reemplazándolos por los objetos nativos del nodo correspondientes. 
 * Cualquier intento de coerción numérica directa (`+d.source`) posterior devuelve `NaN`, rompiendo el renderizado. 
 * Esta función normaliza la lectura abstrayendo si la referencia sigue siendo primitiva o ya es un objeto mutado.
 * @param {(string|number|Object)} d - Atributo `source` o `target` extraído de un enlace físico.
 * @returns {(string|number)} El identificador unificado del nodo (Datapath ID del switch).
 */
function getNodeId(d) {
    return typeof d === "object" && d !== null ? d.id : d;
}

/**
 * @function initTopology
 * @description Inicializa la estructura base del lienzo interactivo SVG y el motor de simulación física D3.
 * @details Configura las dimensiones del contenedor `#topoGraph`, inyecta definiciones de marcadores 
 * vectoriales para trazar rutas direccionales, acopla los manejadores globales para Zoom/Paneo y calibra 
 * los coeficientes físicos óptimos para el mapa de ~23 switches de la red académica troncal GÉANT.
 */
function initTopology() {
    const container = document.getElementById("topoGraph");
    const W = container.clientWidth  || 860;
    const H = container.clientHeight || 520;

    // Inicializar contenedor SVG principal con propiedades de escalabilidad reactiva
    svgRef = d3.select("#topoGraph")
        .append("svg")
        .attr("width",  "100%")
        .attr("height", "100%")
        .attr("viewBox", `0 0 ${W} ${H}`)
        .style("display", "block");

    const g = svgRef.append("g").attr("class", "zoom-group");

    // Inyección en defs de la punta de flecha (marker) para resaltar visualmente los flujos de Dijkstra
    svgRef.append("defs").append("marker")
        .attr("id", "arrow")
        .attr("viewBox", "0 -4 8 8")
        .attr("refX", 20).attr("refY", 0)
        .attr("markerWidth", 6).attr("markerHeight", 6)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-4L8,0L0,4")
        .attr("fill", "#3498DB");

    // Subgrupos estructurales para garantizar orden de capas (enlaces al fondo, nodos al frente)
    g.append("g").attr("class", "links-g");
    g.append("g").attr("class", "nodes-g");

    // Configuración paramétrica de la matriz de Zoom y Paneo
    zoomBehavior = d3.zoom()
        .scaleExtent([0.15, 4]) // Límites de alejamiento y acercamiento
        .on("zoom", (event) => {
            g.attr("transform", event.transform);
        });

    svgRef.call(zoomBehavior);
    svgRef.on("dblclick.zoom", () => centerTopology()); // Doble clic ejecuta auto-enfoque

    // Calibración precisa de fuerzas de conmutación para evitar oscilaciones o solapamiento de switches
    d3Sim = d3.forceSimulation()
        .force("link",      d3.forceLink().id(d => d.id).distance(90).strength(0.5))
        .force("charge",    d3.forceManyBody().strength(-180).distanceMax(300))
        .force("center",    d3.forceCenter(W / 2, H / 2))
        .force("collision", d3.forceCollide(26)) // Radio de protección anti-colisión
        .alphaDecay(0.03)   // Controla la tasa de enfriamiento de la simulación
        .velocityDecay(0.4); // Coeficiente de fricción virtual del lienzo

    // Inyección dinámica del botón flotante para recentrar la cámara de red
    d3.select("#topoGraph")
        .append("button")
        .attr("id", "btnCenter")
        .style("position",   "absolute")
        .style("top",        "10px")
        .style("right",      "10px")
        .style("z-index",    "10")
        .style("background", "#1e2a3a")
        .style("color",      "#DD9C0F")
        .style("border",     "1px solid #DD9C0F")
        .style("padding",    "5px 12px")
        .style("cursor",     "pointer")
        .style("font-size",  "12px")
        .style("border-radius", "4px")
        .text("⊙ Centrar")
        .on("click", () => centerTopology());

    topoInited = true;
}

/**
 * @function fitToView
 * @description Calcula la caja delimitadora (*bounding box*) del grafo real y traslada la cámara para centrarla.
 * @details Evalúa las posiciones cartesianas instantáneas (mínimos y máximos de `x` e `y`) de todos los nodos en 
 * el plano virtual de D3, calcula la escala matemática óptima con márgenes de holgura (*padding*) y dispara 
 * una transición animada suave de 600ms para ajustar el zoom de la interfaz.
 */
function fitToView() {
    if (!svgRef || d3Nodes.length === 0) return;

    const container = document.getElementById("topoGraph");
    const W   = container.clientWidth  || 860;
    const H   = container.clientHeight || 520;
    const pad = 60; // Margen perimetral de seguridad

    const xs   = d3Nodes.map(d => d.x || 0);
    const ys   = d3Nodes.map(d => d.y || 0);
    const xMin = Math.min(...xs) - pad;
    const xMax = Math.max(...xs) + pad;
    const yMin = Math.min(...ys) - pad;
    const yMax = Math.max(...ys) + pad;

    const bW = xMax - xMin;
    const bH = yMax - yMin;
    if (bW === 0 || bH === 0) return;

    // Determinar factor de conversión adaptativo sin superar un límite máximo de 2.5 aumentos
    const scale = Math.min(W / bW, H / bH, 2.5);
    const tx    = (W - scale * (xMin + xMax)) / 2;
    const ty    = (H - scale * (yMin + yMax)) / 2;

    svgRef.transition()
        .duration(600)
        .call(
            zoomBehavior.transform,
            d3.zoomIdentity.translate(tx, ty).scale(scale)
        );
}

/**
 * @function centerTopology
 * @description Función interfaz delegada que gatilla el re-enfoque adaptativo del lienzo de red.
 */
function centerTopology() { fitToView(); }

/**
 * @function weightColor
 * @description Generador de mapas de color dinámicos según el peso operativo de los enlaces físicos.
 * @details Traduce las métricas de ponderación del algoritmo Dijkstra en una escala cromática de semáforo 
 * para facilitar al operador la detección visual inmediata de congestión o penalizaciones en la infraestructura:
 * - **Peso <= 3**: Verde (`#2ECC71`), canal óptimo / métrica estándar de saltos.
 * - **Peso <= 8**: Naranja (`#F39C12`), degradación moderada o costos intermedios por políticas.
 * - **Peso > 8**: Rojo (`#DD0F50`), canal severamente penalizado por congestión o políticas restrictivas.
 * @param {number} w - Costo o peso del enlace devuelto por el controlador.
 * @returns {string} Código hexadecimal representativo del color.
 */
function weightColor(w) {
    if (w === undefined || w === null) return "#555";
    if (w <= 3)  return "#2ECC71";
    if (w <= 8)  return "#F39C12";
    return "#DD0F50";
}

/**
 * @function isOnActivePath
 * @description Evalúa si un enlace específico inter-switch forma parte de la traza calculada por el backend.
 * @param {(string|number)} src - ID del switch de origen del enlace examinado.
 * @param {(string|number)} dst - ID del switch de destino del enlace examinado.
 * @returns {boolean} Verdadero si la conexión bidireccional se encuentra contenida en el vector de la ruta activa.
 */
function isOnActivePath(src, dst) {
    if (activeRoutePath.length < 2) return false;
    for (let i = 0; i < activeRoutePath.length - 1; i++) {
        const a = activeRoutePath[i];
        const b = activeRoutePath[i + 1];
        if ((a === src && b === dst) || (a === dst && b === src)) return true;
    }
    return false;
}

/** @var {string} _lastTopoHash Caché de control hash para mitigar re-cálculos estructurales destructivos en la física de grafos */
let _lastTopoHash = "";

/**
 * @function topoHash
 * @description Genera una firma string determinista que representa la topología actual de nodos y aristas.
 * @param {Array<number|string>} nodes - Arreglo secuencial de switches.
 * @param {Array<Object>} edges - Arreglo de conexiones físicas.
 * @returns {string} Hash serializado único para auditoría de cambios estructurales.
 */
function topoHash(nodes, edges) {
    return nodes.sort((a, b) => a - b).join(",") + "|" +
           edges.map(e => `${e.source}-${e.target}`).sort().join(",");
}

/**
 * @function updateTopology
 * @description Sincroniza, enlaza y renderiza reactivamente las mutaciones de la red física GÉANT vía D3 Data Binding.
 * @details Consume el payload JSON del API REST, preserva las coordenadas cartesianas previas de los switches 
 * (`existingById`) para mitigar el molesto efecto de parpadeo y utiliza funciones key normalizadas de ordenamiento 
 * `menor-mayor` para unificar los canales full-duplex (`A-B` y `B-A`), evitando la duplicidad visual de aristas.
 * En cada ciclo de refresco estructural (*structureChanged*), re-inyecta energía al motor de fuerza física (`alpha`) 
 * y dispara un auto-enfoque cinético al finalizar la estabilización del grafo.
 * @param {Object} data - Payload de red.
 * @param {Array<number|string>} data.nodes - Listado puro de identificadores de switches.
 * @param {Array<Object>} [data.links] - Colección opcional de enlaces de la infraestructura.
 * @param {Array<Object>} [data.edges] - Nomenclatura alternativa para la colección de enlaces de la infraestructura.
 */
function updateTopology(data) {
    if (!data || !data.nodes) return;
    if (!topoInited) return;

    const rawEdges = data.links || data.edges || [];

    // Validar si existieron adiciones o remociones de hardware físico en la red académica
    const hash = topoHash([...data.nodes], [...rawEdges]);
    const structureChanged = (hash !== _lastTopoHash);
    _lastTopoHash = hash;

    // Preservar de forma persistente la posición geográfica previa de los nodos para estabilidad visual
    const existingById = {};
    d3Nodes.forEach(n => { existingById[n.id] = n; });

    d3Nodes = data.nodes.map(id => existingById[id] || {
        id,
        x: undefined,
        y: undefined
    });

    // Mapeo defensivo de aristas usando variables estables
    d3Links = rawEdges.map(e => ({
        source: e.source !== undefined ? e.source : e.src,
        target: e.target !== undefined ? e.target : e.dst,
        weight: e.weight
    }));

    const zoomG = svgRef.select(".zoom-group");

    // A) ENLAZADO DE ARISTAS (LINKS DATA BINDING)
    const linkKey = d => {
        const s = getNodeId(d.source);
        const t = getNodeId(d.target);
        return `${Math.min(+s, +t)}-${Math.max(+s, +t)}`;
    };

    const linkSel = zoomG.select(".links-g")
        .selectAll("line")
        .data(d3Links, linkKey);

    const linkEnter = linkSel.enter()
        .append("line")
        .attr("stroke-opacity", 0.75)
        .attr("stroke-width", 1.8);

    // Actualización de atributos visuales (Color y Grosor) ante cambios de pesos o activación de rutas
    linkEnter.merge(linkSel)
        .attr("stroke", d => {
            const s = getNodeId(d.source);
            const t = getNodeId(d.target);
            return isOnActivePath(s, t) ? "#3498DB" : weightColor(d.weight);
        })
        .attr("stroke-width", d => {
            const s = getNodeId(d.source);
            const t = getNodeId(d.target);
            return isOnActivePath(s, t) ? 3.5 : 1.8; // Engrosar canal si contiene el camino de Dijkstra
        });

    linkSel.exit().remove();

    // B) ENLAZADO DE VÉRTICES (NODES DATA BINDING)
    const nodeSel = zoomG.select(".nodes-g")
        .selectAll("g.node-g")
        .data(d3Nodes, d => d.id);

    const nodeEnter = nodeSel.enter()
        .append("g")
        .attr("class", "node-g")
        .style("cursor", "pointer")
        .call(
            d3.drag()
                .on("start", (event, d) => {
                    // Despertar cinético del motor de fuerzas al iniciar el arrastre manual
                    if (!event.active) d3Sim.alphaTarget(0.2).restart();
                    d.fx = d.x;
                    d.fy = d.y;
                })
                .on("drag", (event, d) => {
                    d.fx = event.x;
                    d.fy = event.y;
                })
                .on("end", (event, d) => {
                    // Devolver el control de fuerzas autónomo al soltar el ratón
                    if (!event.active) d3Sim.alphaTarget(0);
                })
        )
        .on("dblclick", (event, d) => {
            // Doble clic remueve el anclaje físico fijo (libera el nodo en el espacio)
            event.stopPropagation();
            d.fx = null;
            d.fy = null;
        });

    nodeEnter.append("circle").attr("r", 14);
    nodeEnter.append("text")
        .attr("text-anchor", "middle")
        .attr("dy", "0.35em")
        .attr("font-size", "9px")
        .attr("fill", "#FFFFFF")
        .attr("pointer-events", "none");

    const nodeMerge = nodeEnter.merge(nodeSel);

    // Resaltar cromaticamente en azul si el switch es una escala del camino Dijkstra activo
    nodeMerge.select("circle")
        .attr("fill",         d => activeRoutePath.includes(d.id) ? "#3498DB" : "#2C3E50")
        .attr("stroke",       d => activeRoutePath.includes(d.id) ? "#3498DB" : "#DD9C0F")
        .attr("stroke-width", d => activeRoutePath.includes(d.id) ? 3 : 1.5);

    nodeMerge.select("text")
        .text(d => `S${d.id}`);

    nodeSel.exit().remove();

    // C) EJECUCIÓN Y ORQUESTACIÓN FÍSICA (TICK LOOP)
    d3Sim.nodes(d3Nodes);
    d3Sim.force("link").links(d3Links);

    // Loop interno que actualiza las coordenadas vectoriales en cada frame de renderizado animado
    d3Sim.on("tick", () => {
        zoomG.select(".links-g").selectAll("line")
            .attr("x1", d => (d.source.x || 0))
            .attr("y1", d => (d.source.y || 0))
            .attr("x2", d => (d.target.x || 0))
            .attr("y2", d => (d.target.y || 0));

        zoomG.select(".nodes-g").selectAll("g.node-g")
            .attr("transform", d => `translate(${d.x || 0},${d.y || 0})`);
    });

    // Si la estructura mutó formalmente, recalienta el motor para ordenar los nuevos elementos
    if (structureChanged) {
        d3Sim.alpha(0.4).restart();
        d3Sim.on("end", () => fitToView());
    }
}

/**
 * @async
 * @function pollTopology
 * @description Hilo de polling encargado de solicitar de forma periódica el layout estructural de la red GÉANT.
 */
async function pollTopology() {
    try {
        const data = await apiFetch("/api/topology");
        updateTopology(data);
    } catch (e) {
        console.warn("Topology poll failed:", e.message);
    }
}

// 6. METRICS DISPLAY (TELEMETRÍA Y PROCESAMIENTO TEMPORAL)
/**
 * @function renderMetricCard
 * @description Genera una fila estructurada en HTML (`<tr>`) con formato condicional según los umbrales de red.
 * @details Modifica dinámicamente las clases CSS (`danger`, `warning`, `highlight`) tras evaluar el estado operacional 
 * de un puerto de switch OpenFlow. Muestra indicadores críticos como pérdidas de paquetes, tasas de descarte y el peso Dijkstra actual.
 * * Lógica de Umbrales y Alertas Visuales:
 * - **Pérdida de paquetes (`packet_loss`)**: `.danger` si > 10%, `.warning` si > 2%, `.ok` en condiciones óptimas.
 * - **Throughput (`throughput_mbps`)**: `.highlight` si detecta flujo activo (>= 0.01 Mbps), `.warning` si supera los 50 Mbps.
 * - **Utilización de canal (`utilization_pct`)**: `.danger` si supera el 80% (congestión severa), `.warning` si es > 50%.
 * @param {string} key - Clave única indexadora del puerto.
 * @param {Object} m - Objeto de métricas individuales por puerto.
 * @param {number} [m.packet_loss=0] - Porcentaje de pérdida de paquetes.
 * @param {number} [m.throughput_mbps=0] - Tasa de transferencia en Megabits por segundo.
 * @param {number} [m.weight=1] - Costo dinámico asignado al enlace en la matriz Dijkstra.
 * @param {number} [m.utilization_pct=0] - Porcentaje de uso del ancho de banda nominal.
 * @param {number} [m.jitter_est_ms=0] - Estimación del Jitter de red en milisegundos.
 * @param {number} [m.pps_rx=0] - Paquetes por segundo recibidos.
 * @param {number} [m.pps_tx=0] - Paquetes por segundo transmitidos.
 * @param {number} [m.bytes_per_s=0] - Bytes totales por segundo.
 * @param {number} [m.rx_dropped=0] - Conteo de descarte en recepción.
 * @param {number} [m.tx_dropped=0] - Conteo de descarte en transmisión.
 * @param {number} [m.rx_errors=0] - Errores de hardware en recepción.
 * @param {number} [m.tx_errors=0] - Errores de hardware en transmisión.
 * @param {string} [m.traffic_type='DEFAULT'] - Clasificación del flujo detectada por Machine Learning.
 * @returns {string} Fila HTML formateada para su inyección directa en el DOM.
 */
function renderMetricCard(key, m) {
    const loss  = m.packet_loss     || 0;
    const thr   = m.throughput_mbps || 0;
    const w     = m.weight          || 1;
    const util  = m.utilization_pct || 0;
    const jit   = m.jitter_est_ms   || 0;
    const ppsRx = m.pps_rx          || 0;
    const ppsTx = m.pps_tx          || 0;
    const bps   = m.bytes_per_s     || 0;
    const rxD   = m.rx_dropped      || 0;
    const txD   = m.tx_dropped      || 0;
    const rxE   = m.rx_errors       || 0;
    const txE   = m.tx_errors       || 0;
    const dropR = m.drop_rate_pps   || 0;

    const lossClass  = loss > 10  ? "danger" : loss > 2   ? "warning" : "ok";
    const thrClass   = thr  < 0.01 ? ""       : thr > 50  ? "warning" : "highlight";
    const utilClass  = util > 80  ? "danger"  : util > 50  ? "warning" : "";
    const badge      = m.traffic_type || "DEFAULT";

    return `
    <tr class="metric-row-data">
        <td><span class="traffic-badge ${badge}">${badge}</span></td>
        <td style="font-family:monospace;color:var(--gold)">SW${m.switch}·P${m.port}</td>
        <td class="${thrClass}">${thr.toFixed(3)}</td>
        <td class="${lossClass}">${loss.toFixed(2)}%</td>
        <td class="${utilClass}">${util.toFixed(1)}%</td>
        <td>${jit.toFixed(1)}</td>
        <td>${Math.round(ppsRx + ppsTx)}</td>
        <td>${(bps/1024).toFixed(1)}</td>
        <td style="color:#e74c3c">${rxD + txD}</td>
        <td style="color:#e67e22">${rxE + txE}</td>
        <td style="color:var(--gold);font-weight:700">${w.toFixed(2)}</td>
    </tr>`;
}

// VENTANAS HISTÓRICAS AGREGADAS GLOBALES

/** @var {Array<number>} historyLoss Búfer circular histórico para el porcentaje promedio de pérdida */
const historyLoss  = Array(MAX_HISTORY).fill(0);
/** @var {Array<number>} historyUtil Búfer circular histórico para la utilización global de la infraestructura */
const historyUtil  = Array(MAX_HISTORY).fill(0);
/** @var {Array<number>} historyJitter Búfer circular histórico para el Jitter acumulado en milisegundos */
const historyJitter = Array(MAX_HISTORY).fill(0);

/**
 * @async
 * @function pollMetrics
 * @description Consume el endpoint asíncrono de telemetría de red, procesa agregaciones estadísticas y actualiza la UI.
 * @details Divide las métricas brutas del API agrupándolas jerárquicamente por Switch para renderizar una vista de 
 * acordeones interactivos (`.sw-accordion`). De forma paralela, calcula promedios generales e inyecta los datos en 
 * los arreglos de historial móvil, empujando (*push*) la nueva muestra y extrayendo (*shift*) la más antigua para mantener 
 * la ventana fija de 40 muestras en los gráficos lineales de Chart.js sin degradar la memoria del navegador.
 */
async function pollMetrics() {
    try {
        const metrics  = await apiFetch("/api/metrics");
        const entries  = Object.entries(metrics);
        const container = document.getElementById("metricsSummary");

        if (entries.length === 0) {
            container.innerHTML = '<div class="empty-state">Sin métricas disponibles. Genera tráfico entre hosts (ping, iperf).</div>';
        } else {
            // A) AGRUPACIÓN ESTRUCTURAL POR DATAPATH ID (SWITCH)
            const bySw = {};
            for (const [k, m] of entries) {
                const sw = m.switch;
                if (!bySw[sw]) bySw[sw] = [];
                bySw[sw].push([k, m]);
            }
            const swIds = Object.keys(bySw).sort((a, b) => +a - +b);

            // Inyección dinámica de acordeones con cálculo de estado en la cabecera
            container.innerHTML = swIds.map(sw => {
                const ports = bySw[sw];
                const totalThr  = ports.reduce((s, [, m]) => s + (m.throughput_mbps || 0), 0);
                const avgLoss   = ports.reduce((s, [, m]) => s + (m.packet_loss || 0), 0) / ports.length;
                const avgUtil   = ports.reduce((s, [, m]) => s + (m.utilization_pct || 0), 0) / ports.length;
                
                // Determinar el nivel de criticidad general del switch
                const statusCls = avgLoss > 5 ? "sw-danger" : avgUtil > 70 ? "sw-warn" : "sw-ok";
                
                return `
                <div class="sw-accordion">
                    <div class="sw-accordion-header" onclick="toggleAccordion('sw-${sw}')">
                        <span class="sw-label">S<strong>${sw}</strong></span>
                        <span class="sw-summary">
                            <span class="sw-badge ${statusCls}">
                                ${avgLoss > 5 ? "⚠ Loss" : avgUtil > 70 ? "~ High" : "✔ OK"}
                            </span>
                            <span style="color:var(--gold)">${totalThr.toFixed(3)} Mbps</span>
                            <span style="color:#888">|</span>
                            <span class="${avgLoss > 5 ? 'danger' : ''}">Loss: ${avgLoss.toFixed(2)}%</span>
                            <span style="color:#888">|</span>
                            <span>${ports.length} puertos</span>
                        </span>
                        <span class="sw-chevron" id="chev-sw-${sw}">▼</span>
                    </div>
                    <div class="sw-accordion-body" id="sw-${sw}">
                        <div class="metrics-table-wrap">
                        <table class="metrics-compact-table">
                            <thead><tr>
                                <th>Tráfico</th><th>Puerto</th>
                                <th>Thr (Mbps)</th><th>Loss</th><th>Util%</th>
                                <th>Jitter(ms)</th><th>PPS</th><th>KB/s</th>
                                <th>Drops</th><th>Errores</th><th>Peso</th>
                            </tr></thead>
                            <tbody>
                                ${ports.map(([k, m]) => renderMetricCard(k, m)).join("")}
                            </tbody>
                        </table>
                        </div>
                    </div>
                </div>`;
            }).join("");
        }

        // B) AGREGACIÓN DE INSTANTE PARA GRÁFICOS DE BARRAS DE RENDIMIENTO
        const bySwitch = {};
        for (const [, m] of entries) {
            const sw = m.switch;
            if (!bySwitch[sw]) bySwitch[sw] = { thr: 0, loss: 0, util: 0, jitter: 0, count: 0 };
            bySwitch[sw].thr    += m.throughput_mbps || 0;
            bySwitch[sw].loss   += m.packet_loss     || 0;
            bySwitch[sw].util   += m.utilization_pct || 0;
            bySwitch[sw].jitter += m.jitter_est_ms   || 0;
            bySwitch[sw].count++;
        }

        const swIdsChart = Object.keys(bySwitch).sort((a, b) => +a - +b);
        const thrVals = swIdsChart.map(s => bySwitch[s].thr.toFixed(3));
        const lossVals = swIdsChart.map(s => (bySwitch[s].loss / bySwitch[s].count).toFixed(3));
        const labels  = swIdsChart.map(s => `S${s}`);

        // Actualizar gráficos de barras usando actualización optimizada sin animaciones disruptivas ("none")
        if (bwChartInst) {
            bwChartInst.data.labels = labels;
            bwChartInst.data.datasets[0].data = thrVals;
            bwChartInst.update("none");
        }
        if (lossChartInst) {
            lossChartInst.data.labels = labels;
            lossChartInst.data.datasets[0].data = lossVals;
            lossChartInst.update("none");
        }

        // C) CÁCULO DE MEDIA MÓVIL GLOBAL PARA GRÁFICOS LINEALES HISTÓRICOS
        const totalThr  = entries.reduce((sum, [, m]) => sum + (m.throughput_mbps || 0), 0);
        const avgLoss   = entries.length ? entries.reduce((sum, [, m]) => sum + (m.packet_loss || 0), 0) / entries.length : 0;
        const avgUtil   = entries.length ? entries.reduce((sum, [, m]) => sum + (m.utilization_pct || 0), 0) / entries.length : 0;
        const avgJitter = entries.length ? entries.reduce((sum, [, m]) => sum + (m.jitter_est_ms || 0), 0) / entries.length : 0;

        const now = new Date().toLocaleTimeString();
        historyLabels.push(now);
        historyLabels.shift();

        // Operación FIFO en arreglos históricos para simular persistencia temporal reactiva
        if (!historyDatasets["total"]) historyDatasets["total"] = Array(MAX_HISTORY).fill(0);
        historyDatasets["total"].push(parseFloat(totalThr.toFixed(4)));
        historyDatasets["total"].shift();

        historyLoss.push(parseFloat(avgLoss.toFixed(4)));     historyLoss.shift();
        historyUtil.push(parseFloat(avgUtil.toFixed(4)));     historyUtil.shift();
        historyJitter.push(parseFloat(avgJitter.toFixed(4))); historyJitter.shift();

        // Empujar datos y sincronizar vistas históricas analíticas
        if (historyChartInst) {
            historyChartInst.data.labels = [...historyLabels];
            historyChartInst.data.datasets[0].data = [...historyDatasets["total"]];
            historyChartInst.update("none");
        }
        if (historyLossChartInst) {
            historyLossChartInst.data.labels = [...historyLabels];
            historyLossChartInst.data.datasets[0].data = [...historyLoss];
            historyLossChartInst.update("none");
        }
        if (historyUtilChartInst) {
            historyUtilChartInst.data.labels = [...historyLabels];
            historyUtilChartInst.data.datasets[0].data = [...historyUtil];
            historyUtilChartInst.update("none");
        }

    } catch (e) {
        console.warn("Metrics poll failed:", e.message);
    }
}

/**
 * @function toggleAccordion
 * @description Alterna la visibilidad visual de las tablas de puertos internas de un switch.
 * @details Conmuta la clase CSS `.open` en el cuerpo del acordeón e intercambia la orientación del glifo indicador.
 * @param {string} id - Selector único del cuerpo del acordeón afectado (ej: 'sw-1').
 */
function toggleAccordion(id) {
    const body = document.getElementById(id);
    const chev = document.getElementById(`chev-${id}`);
    if (!body) return;
    const open = body.classList.toggle("open");
    if (chev) chev.textContent = open ? "▲" : "▼";
}

// 7. ML PREDICTIONS DISPLAY (PANEL DE INFERENCIA INTELIGENTE)
/**
 * @async
 * @function pollPredictions
 * @description Hilo de ejecución periódico encargado de consultar, mapear y graficar las predicciones de Machine Learning.
 * @details Recupera las clasificaciones en caliente del tráfico de red desde el modelo RandomForest del backend. 
 * Realiza un procesamiento de agregación por tipo de servicio para calcular la distribución absoluta de flujos y 
 * promediar sus costos de enrutamiento asociados. Posteriormente, actualiza de manera síncrona tres vistas críticas:
 * 1. **Métricas en Bloques (`#mlOverview`)**: Inyecta tarjetas dinámicas con el peso acumulado por cada tipo de tráfico.
 * 2. **Gráficos Estadísticos (Chart.js)**: Alimenta el gráfico de torta de distribución (`pieChartInst`) y el de barras de peso (`barChartInst`) omitiendo animaciones mediante `"none"`.
 * 3. **Auditoría Compacta (`#mlTable`)**: Renderiza una tabla detallada puerto por puerto con estampas de tiempo legibles de la inferencia.
 * @throws {Error} Advierte en la consola del navegador si hay un fallo en la conexión con el endpoint de analíticas de IA.
 */
async function pollPredictions() {
    try {
        const preds   = await apiFetch("/api/predictions");
        const entries = Object.entries(preds);

        // A) PROCESAMIENTO Y AGRUPACIÓN POR CATEGORÍA DE TRÁFICO ML
        const byType = {};
        for (const [, p] of entries) {
            const t = p.traffic_type || "UNKNOWN";
            if (!byType[t]) byType[t] = { count: 0, totalWeight: 0 };
            byType[t].count++;
            byType[t].totalWeight += p.weight || 0;
        }

        const overview = document.getElementById("mlOverview");
        if (Object.keys(byType).length === 0) {
            overview.innerHTML = '<div class="empty-state">Sin predicciones todavía. Genera tráfico entre hosts.</div>';
        } else {
            // Renderizado interactivo de tarjetas analíticas por aplicación
            overview.innerHTML = Object.entries(byType).map(([type, data]) => `
                <div class="ml-card">
                    <div class="ml-card-type">${type}</div>
                    <div class="ml-card-label">Tipo detectado</div>
                    <div class="ml-card-weight">${(data.totalWeight / data.count).toFixed(2)}</div>
                    <div class="ml-card-label">Peso promedio</div>
                    <div style="margin-top:6px; color:#888; font-size:0.8rem">${data.count} puertos</div>
                </div>
            `).join("");
        }

        // B) SINCRONIZACIÓN REACTIVA DEL GRÁFICO DE PASTA / DONA (DISTRIBUCIÓN DE TRÁFICO)
        const pieLabels = Object.keys(byType);
        const pieCounts = pieLabels.map(t => byType[t].count);
        const pieColors = ["#3498DB", "#2ECC71", "#F39C12", "#E74C3C", "#9B59B6", "#1ABC9C"];

        if (pieChartInst) {
            pieChartInst.data.labels = pieLabels;
            pieChartInst.data.datasets[0].data = pieCounts;
            // Segmentar la paleta de colores fija según la cantidad de clases presentes en la red
            pieChartInst.data.datasets[0].backgroundColor = pieColors.slice(0, pieLabels.length);
            pieChartInst.update("none");
        }

        // C) SINCRONIZACIÓN DEL GRÁFICO DE BARRAS (COSTOS/PESOS PROMEDIO POR CLASE)
        const barWeights = pieLabels.map(t => (byType[t].totalWeight / byType[t].count).toFixed(2));
        if (barChartInst) {
            barChartInst.data.labels = pieLabels;
            barChartInst.data.datasets[0].data = barWeights;
            barChartInst.update("none");
        }

        // D) RENDERIZADO DEL REGISTRO DE AUDITORÍA DETALLADO (LOG DE INFERENCIA MULTI-PUERTO)
        const mlTable = document.getElementById("mlTable");
        if (entries.length === 0) {
            mlTable.innerHTML = '<div class="empty-state">Sin datos de predicción.</div>';
        } else {
            mlTable.innerHTML = `
            <table>
                <thead><tr>
                    <th>Puerto</th><th>Tráfico</th>
                    <th>Throughput (Mbps)</th><th>Pérdida (%)</th>
                    <th>Peso</th><th>Timestamp</th>
                </tr></thead>
                <tbody>
                ${entries.map(([k, p]) => `
                    <tr>
                        <td style="font-family:monospace; font-weight:600">SW${p.switch} · P${p.port}</td>
                        <td><span class="traffic-badge ${p.traffic_type || 'DEFAULT'}">${p.traffic_type || '?'}</span></td>
                        <td>${(p.throughput_mbps || 0).toFixed(4)}</td>
                        <td>${(p.packet_loss || 0).toFixed(2)}%</td>
                        <td style="color:#DD9C0F; font-weight:700">${(p.weight || 0).toFixed(2)}</td>
                        <td style="color:#666; font-size:0.78rem">${p.timestamp ? new Date(p.timestamp * 1000).toLocaleTimeString() : '—'}</td>
                    </tr>
                `).join("")}
                </tbody>
            </table>`;
        }

    } catch (e) {
        console.warn("Predictions poll failed:", e.message);
    }
}

// 8. CHARTS INIT (ORQUESTACIÓN Y CONFIGURACIÓN DE CHART.JS)
/**
 * @function initCharts
 * @description Inicializa y configura de forma estática todas las instancias de gráficos analíticos (Chart.js) del dashboard.
 * @details Construye e inyecta en los contextos 2D de HTML5 Canvas las estructuras de visualización para telemetría:
 * 1. **bwChartInst (Barra)**: Rendimiento instantáneo por Switch en Mbps.
 * 2. **lossChartInst (Barra)**: Porcentaje instantáneo de pérdida de paquetes por Switch.
 * 3. **historyChartInst (Línea)**: Historial temporal acumulado del Throughput total de la red.
 * 4. **historyLossChartInst (Línea)**: Historial de la media móvil de pérdida de paquetes (Escala fijada de 0 a 100%).
 * 5. **historyUtilChartInst (Línea)**: Evolución del porcentaje de utilización de los enlaces físicos OpenFlow.
 * 6. **pieChartInst (Dona/Doughnut)**: Distribución volumétrica de las clases de tráfico clasificadas por el modelo de IA.
 * 7. **barChartInst (Barra Horizontal)**: Costos o pesos promedio calculados por Dijkstra según el tipo de aplicación (`indexAxis: "y"`).
 * * * Optimización Crítica de Rendimiento:
 * Se fuerza la bandera `animation: false` en los parámetros globales de configuración. Dado que los hilos de *polling* * actualizan los conjuntos de datos cada pocos segundos, deshabilitar las transiciones de dibujo nativas previene fugas de memoria, 
 * picos de uso de CPU y parpadeos visuales disruptivos en el navegador del operador de red.
 */
function initCharts() {
    /** * @constant {Object} chartDefaults 
     * @description Configuración base compartida para homogeneizar estilos visuales y rendimiento de los gráficos.
     */
    const chartDefaults = {
        responsive: true,
        animation: false, // Desactivado de forma intencional para optimizar actualizaciones en tiempo real
        plugins: {
            legend: { 
                labels: { 
                    color: "#FFFFFF", 
                    font: { size: 12, family: "Georgia" } 
                } 
            }
        }
    };

    // A) GRÁFICO INSTANTÁNEO DE ANCHO DE BANDA POR SWITCH
    bwChartInst = new Chart(document.getElementById("bwChart").getContext("2d"), {
        type: "bar",
        data: {
            labels: [],
            datasets: [{
                label: "Throughput (Mbps)",
                data: [],
                backgroundColor: "rgba(221, 156, 15, 0.5)", // Tonalidad dorada traslúcida
                borderColor: "#DD9C0F",
                borderWidth: 1.5
            }]
        },
        options: {
            ...chartDefaults,
            scales: {
                y: { beginAtZero: true, ticks: { color: "#FFF" }, grid: { color: "rgba(255,255,255,0.06)" } },
                x: { ticks: { color: "#FFF" }, grid: { color: "rgba(255,255,255,0.03)" } }
            }
        }
    });

    // B) GRÁFICO INSTANTÁNEO DE PÉRDIDA DE PAQUETES POR SWITCH
    lossChartInst = new Chart(document.getElementById("lossChart").getContext("2d"), {
        type: "bar",
        data: {
            labels: [],
            datasets: [{
                label: "Pérdida (%)",
                data: [],
                backgroundColor: "rgba(221, 15, 80, 0.5)", // Tonalidad carmín/alerta traslúcida
                borderColor: "#DD0F50",
                borderWidth: 1.5
            }]
        },
        options: {
            ...chartDefaults,
            scales: {
                y: { beginAtZero: true, ticks: { color: "#FFF" }, grid: { color: "rgba(255,255,255,0.06)" } },
                x: { ticks: { color: "#FFF" }, grid: { color: "rgba(255,255,255,0.03)" } }
            }
        }
    });

    /** * @constant {Object} lineBase 
     * @description Plantilla estructural para los gráficos temporales lineales con límites de muestreo FIFO.
     */
    const lineBase = {
        type: "line",
        options: {
            ...chartDefaults,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { color: "#FFF" },
                    grid: { color: "rgba(255,255,255,0.06)" }
                },
                x: {
                    ticks: { color: "#FFF", font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
                    grid: { color: "rgba(255,255,255,0.03)" },
                    title: { display: true, text: "Tiempo", color: "#DD9C0F" }
                }
            }
        }
    };

    // C) HISTORIAL TEMPORAL: ANCHO DE BANDA GLOBAL
    historyChartInst = new Chart(document.getElementById("historyChart").getContext("2d"), {
        type: "line",
        data: {
            labels: [...historyLabels],
            datasets: [{
                label: "Throughput Total (Mbps)",
                data: [...(historyDatasets["total"] || Array(MAX_HISTORY).fill(0))],
                borderColor: "#DD9C0F",
                backgroundColor: "rgba(221, 156, 15, 0.08)",
                borderWidth: 2,
                pointRadius: 2,
                tension: 0.35, // Suavizado de curva tipo Spline cúbica
                fill: true
            }]
        },
        options: {
            ...lineBase.options,
            scales: {
                ...lineBase.options.scales,
                y: { ...lineBase.options.scales.y, title: { display: true, text: "Mbps", color: "#DD9C0F" } }
            }
        }
    });

    // D) HISTORIAL TEMPORAL: PÉRDIDA PROMEDIO
    historyLossChartInst = new Chart(document.getElementById("historyLossChart").getContext("2d"), {
        type: "line",
        data: {
            labels: [...historyLabels],
            datasets: [{
                label: "Pérdida Promedio (%)",
                data: [...historyLoss],
                borderColor: "#DD0F50",
                backgroundColor: "rgba(221, 15, 80, 0.08)",
                borderWidth: 2,
                pointRadius: 2,
                tension: 0.35,
                fill: true
            }]
        },
        options: {
            ...lineBase.options,
            scales: {
                ...lineBase.options.scales,
                y: { ...lineBase.options.scales.y, max: 100, title: { display: true, text: "%", color: "#DD0F50" } }
            }
        }
    });

    // E) HISTORIAL TEMPORAL: UTILIZACIÓN DE LA RED
    historyUtilChartInst = new Chart(document.getElementById("historyUtilChart").getContext("2d"), {
        type: "line",
        data: {
            labels: [...historyLabels],
            datasets: [{
                label: "Utilización Promedio (%)",
                data: [...historyUtil],
                borderColor: "#3498DB",
                backgroundColor: "rgba(52, 152, 219, 0.08)",
                borderWidth: 2,
                pointRadius: 2,
                tension: 0.35,
                fill: true
            }]
        },
        options: {
            ...lineBase.options,
            scales: {
                ...lineBase.options.scales,
                y: { ...lineBase.options.scales.y, max: 100, title: { display: true, text: "%", color: "#3498DB" } }
            }
        }
    });

    // F) DISTRIBUCIÓN DE TRÁFICO (MÓDULO CLASIFICADOR ML)
    pieChartInst = new Chart(document.getElementById("trafficPieChart").getContext("2d"), {
        type: "doughnut", // Formato tipo dona para facilitar la lectura del centro
        data: {
            labels: [],
            datasets: [{
                data: [],
                backgroundColor: ["#3498DB", "#2ECC71", "#F39C12", "#E74C3C", "#9B59B6", "#1ABC9C"],
                borderColor: "#1a1a1a", // Borde oscuro que contrasta con el fondo del panel
                borderWidth: 2
            }]
        },
        options: {
            ...chartDefaults,
            plugins: {
                legend: { position: "right", labels: { color: "#FFF", font: { size: 11 } } }
            }
        }
    });

    // G) PESOS PROMEDIO DIJKSTRA POR APLICACIÓN ML
    barChartInst = new Chart(document.getElementById("weightBarChart").getContext("2d"), {
        type: "bar",
        data: {
            labels: [],
            datasets: [{
                label: "Peso Promedio",
                data: [],
                backgroundColor: ["#3498DB", "#2ECC71", "#F39C12", "#E74C3C", "#9B59B6", "#1ABC9C"],
                borderColor: "transparent",
                borderWidth: 0
            }]
        },
        options: {
            ...chartDefaults,
            indexAxis: "y", // Transforma las barras verticales en horizontales
            scales: {
                x: { beginAtZero: true, ticks: { color: "#FFF" }, grid: { color: "rgba(255,255,255,0.06)" } },
                y: { ticks: { color: "#FFF" }, grid: { color: "rgba(255,255,255,0.03)" } }
            }
        }
    });
}

// 9. ROUTE QUERY (ANÁLISIS DE ENRUTAMIENTO DIJKSTRA Y CACHÉ)
/**
 * @async
 * @function queryRoute
 * @description Envía una solicitud asíncrona al controlador para calcular el camino óptimo entre dos switches.
 * @details Realiza un consumo GET al endpoint `/api/route`, enviando los identificadores de origen (`src`) 
 * y destino (`dst`). Tras validar la respuesta, almacena el vector de saltos en la variable global `activeRoutePath`. 
 * Posteriormente, fuerza un redibujado de la topología D3.js (`updateTopology`), lo que provoca que los enlaces 
 * pertenecientes a esta ruta se iluminen con clases CSS de alta prioridad. Finalmente, renderiza el desglose secuencial 
 * del camino inyectando inline los pesos (`w`) calculados por la métrica de costo dinámico.
 * @throws {Error} Inyecta un bloque de estado `.error-state` en la UI si el backend no puede resolver el camino.
 */
async function queryRoute() {
    const src = parseInt(document.getElementById("routeSrc").value);
    const dst = parseInt(document.getElementById("routeDst").value);
    const resultDiv = document.getElementById("routeResult");

    resultDiv.innerHTML = '<div class="empty-state">Calculando...</div>';

    try {
        const data = await apiFetch(`/api/route?src=${src}&dst=${dst}`);

        if (data.error) {
            resultDiv.innerHTML = `<div class="error-state">${data.error}</div>`;
            return;
        }

        /** @global {Array<number>} activeRoutePath Vector de Datapath IDs que componen la ruta actualmente consultada */
        activeRoutePath = data.path || [];

        // Sincronización forzada del estado visual del grafo D3 con el nuevo camino óptimo
        const currentNodes = d3Nodes.map(n => n.id);
        const currentEdges = d3Links.map(l => ({
            source: getNodeId(l.source),
            target: getNodeId(l.target),
            weight: l.weight
        }));
        updateTopology({ nodes: currentNodes, edges: currentEdges });

        // Mapeo estructurado del camino para su representación en la interfaz de usuario
        const pathHtml = (data.path || []).map((node, i) => {
            const edge = data.edges && data.edges[i - 1];
            const weightLabel = edge
                ? `<span class="route-weight">w=${edge.weight !== null ? edge.weight.toFixed(2) : "?"}</span>`
                : "";
            return `
                ${i > 0 ? `<span class="route-arrow">→</span>` : ""}
                ${i > 0 ? weightLabel : ""}
                <div style="display:inline-flex;flex-direction:column;align-items:center;">
                    <span class="route-node">S${node}</span>
                </div>
            `;
        }).join("");

        resultDiv.innerHTML = `
            <div style="color:#888; font-size:0.8rem; margin-bottom:10px; text-transform:uppercase; letter-spacing:.08em;">
                Ruta óptima S${src} → S${dst} | ${data.path.length} saltos
            </div>
            <div class="route-path">${pathHtml}</div>
        `;

    } catch (e) {
        resultDiv.innerHTML = `<div class="error-state">Error: ${e.message}</div>`;
    }

    // Actualiza inmediatamente el listado de caché tras la consulta
    pollCachedRoutes();
}

/**
 * @async
 * @function pollCachedRoutes
 * @description Consulta el estado actual de la tabla de enrutamiento indexada en la memoria caché del controlador SDN.
 * @details Recupera las rutas activas precalculadas desde el endpoint `/api/routes` para renderizar tarjetas estáticas 
 * de consulta rápida (`.cached-route-card`). Permite al operador observar la persistencia de los caminos optimizados 
 * sin saturar los hilos de ejecución principales de la topología.
 */
async function pollCachedRoutes() {
    try {
        const routes   = await apiFetch("/api/routes");
        const container = document.getElementById("cachedRoutes");
        const entries  = Object.entries(routes);

        if (entries.length === 0) {
            container.innerHTML = '<div class="empty-state">No hay rutas cacheadas.</div>';
            return;
        }

        container.innerHTML = entries.map(([key, r]) => `
            <div class="cached-route-card">
                <div class="cached-route-title">S${r.src} → S${r.dst}</div>
                <div class="route-path">
                    ${(r.path || []).map((n, i) => `
                        ${i > 0 ? '<span class="route-arrow">→</span>' : ''}
                        <span class="route-node">S${n}</span>
                    `).join("")}
                </div>
            </div>
        `).join("");
    } catch (e) {
        console.warn("Cached routes poll failed:", e.message);
    }
}

// 10. POLICY EDITOR (GESTIÓN DE COMPORTAMIENTO Y MULTIPLICADORES QoS)
/** * @var {Object} currentPolicy 
 * @description Copia local de la matriz estructural de políticas activas recuperadas desde el backend.
 */
let currentPolicy = {};

/**
 * @async
 * @function loadPolicies
 * @description Consume la configuración base de QoS e inyecta dinámicamente el formulario del editor de políticas en el DOM.
 * @details Lee la estructura JSON desde `/api/policy`, mapea su estado en el interruptor maestro (`#policyEnabled`), 
 * y itera sobre los parámetros de penalización (ej: multiplicadores de pérdida, ráfagas o throughput) para construir campos 
 * de entrada numéricos formateados (`<input type="number">`) asignándoles un ID compuesto jerárquicamente.
 */
async function loadPolicies() {
    try {
        const data = await apiFetch("/api/policy");
        currentPolicy = data.policy || {};
        document.getElementById("policyEnabled").checked = data.enabled !== false;

        const editor = document.getElementById("policyEditor");
        editor.innerHTML = Object.entries(currentPolicy).map(([type, params]) => `
            <div class="policy-card">
                <div class="policy-card-title">${type}</div>
                ${Object.entries(params).map(([param, value]) => `
                    <div class="policy-field">
                        <label>${param.replace(/_/g, " ")}</label>
                        <input type="number" step="0.1"
                            id="policy_${type}_${param}" value="${value}">
                    </div>
                `).join("")}
            </div>
        `).join("");

        document.getElementById("policyStatus").textContent = "";
    } catch (e) {
        document.getElementById("policyStatus").textContent = "Error cargando políticas.";
        document.getElementById("policyStatus").style.color = "#DD0F50";
    }
}

/**
 * @async
 * @function savePolicies
 * @description Serializa los valores numéricos editados por el usuario y los envía al backend para su aplicación en caliente.
 * @details Recorre de forma recursiva las claves mapeadas en el objeto `currentPolicy`, extrae por ID los valores 
 * flotantes modificados en los inputs del DOM, y realiza una petición POST estructurada hacia `/api/policy`. Esto altera 
 * la ecuación de costos Dijkstra del controlador SDN en tiempo real, modificando de inmediato cómo se comporta el enrutamiento ante incidentes.
 */
async function savePolicies() {
    const updatedPolicy = {};
    for (const [type, params] of Object.entries(currentPolicy)) {
        updatedPolicy[type] = {};
        for (const param of Object.keys(params)) {
            const el = document.getElementById(`policy_${type}_${param}`);
            updatedPolicy[type][param] = el ? parseFloat(el.value) : params[param];
        }
    }
    try {
        await apiPost("/api/policy", {
            policy: updatedPolicy,
            enabled: document.getElementById("policyEnabled").checked
        });
        const status = document.getElementById("policyStatus");
        status.textContent = "✔ Políticas aplicadas";
        status.style.color = "#2ECC71";
        setTimeout(() => { status.textContent = ""; }, 3000);
        currentPolicy = updatedPolicy;
    } catch (e) {
        const status = document.getElementById("policyStatus");
        status.textContent = "Error al aplicar: " + e.message;
        status.style.color = "#DD0F50";
    }
}

/**
 * @async
 * @function togglePolicy
 * @description Habilita o deshabilita de forma binaria el motor de políticas de costos inteligentes en el backend.
 * @details Al desmarcarse, el controlador pasa a ignorar las predicciones del modelo ML y los multiplicadores de penalización, 
 * obligando al algoritmo de enrutamiento a basarse estrictamente en el conteo estático de saltos (Hop-Count clásico).
 */
async function togglePolicy() {
    try {
        await apiPost("/api/policy", {
            enabled: document.getElementById("policyEnabled").checked
        });
    } catch (e) {
        console.warn("Toggle policy failed:", e.message);
    }
}

// 11. CONTROL DE RED — DINÁMICO (FIREWALL DE TERMINALES Y FALLBACK HÍBRIDO)
/**
 * @async
 * @function pollHosts
 * @description Realiza el sondeo periódico del inventario de hosts activos en el plano de datos.
 * @details Consulta el endpoint unificado `/api/hosts`. Si el aprendizaje L2 del controlador SDN no ha registrado 
 * tráfico reactivo (por ejemplo, en un arranque en frío), ejecuta un mecanismo de contingencia (*fallback*) consultando 
 * la topología base (`/api/topology`). A partir de los nodos de conmutación detectados, pre-popula de forma estática 
 * un mapa predictivo de 23 hosts virtuales asignándoles la máscara de direccionamiento IP `10.0.N.1` y el puerto físico `1`.
 * Almacena el resultado final en la colección global `hostsData` y dispara el renderizado de la interfaz.
 */
async function pollHosts() {
    try {
        // El endpoint devuelve el merge de terminales L2 detectadas + mapeo estático de la topología
        let hosts = await apiFetch("/api/hosts");

        if (!hosts || hosts.length === 0) {
            // Último recurso de contingencia si el servicio de inventario del controlador no responde
            const topo = await apiFetch("/api/topology").catch(() => ({ nodes: [] }));
            const nodes = topo.nodes || [];
            if (nodes.length > 0) {
                hosts = nodes.map(n => ({
                    ip:     `10.0.${n}.1`,
                    switch: n,
                    port:   1,
                    status: "allowed",
                    mac:    null,
                    static: true
                }));
            }
        }

        /** @global {Array<Object>} hostsData Almacén global estructurado con el estado de todos los hosts */
        hostsData = hosts;
        renderHostsTable();

    } catch (e) {
        console.warn("pollHosts failed:", e.message);
    }
}

/**
 * @function renderHostsTable
 * @description Renderiza dinámicamente en el DOM la tabla de administración del cortafuegos de hosts.
 * @details Evalúa la variable global de búsqueda `hostFilter` y ejecuta un filtrado multi-criterio en tiempo real 
 * comparando por coincidencia parcial de subcadenas la IP, el Switch de anclaje y la dirección MAC física. 
 * Construye una estructura tabular inyectando componentes interactivos, clases de estado contextuales (`.status-blocked`, 
 * `.status-allowed`) y etiquetas de origen (`L2 detectado` vs `Topología`) para cada host terminal.
 */
function renderHostsTable() {
    const container = document.getElementById("hostsTableContainer");
    if (!container) return;

    const filter = hostFilter.toLowerCase();
    const filtered = hostsData.filter(h => {
        const ip  = (h.ip  || "").toLowerCase();
        const sw  = String(h.switch || "");
        const mac = (h.mac || "").toLowerCase();
        return !filter || ip.includes(filter) || sw.includes(filter) || mac.includes(filter);
    });

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                ${hostsData.length === 0
                    ? "Conectando con el controlador..."
                    : "No hay hosts que coincidan con el filtro."}
            </div>`;
        return;
    }

    container.innerHTML = `
        <table class="hosts-table">
            <thead>
                <tr>
                    <th>Host</th>
                    <th>IP</th>
                    <th>Switch</th>
                    <th>Puerto</th>
                    <th>MAC</th>
                    <th>Tipo</th>
                    <th>Estado</th>
                    <th>Acciones</th>
                </tr>
            </thead>
            <tbody>
                ${filtered.map((h, i) => {
                    const swNum = h.switch || (i + 1);
                    const hostLabel = h.mac
                        ? `h${swNum} (${h.mac.slice(-5)})`
                        : `h${swNum}`;
                    const statusClass = h.status === "blocked" ? "status-blocked" : "status-allowed";
                    const statusText  = h.status === "blocked" ? "Bloqueado" : "Permitido";
                    const typeLabel   = h.static === false ? "L2 detectado" : "Topología";
                    const typeCls     = h.static === false ? "type-dynamic" : "type-static";
                    return `
                    <tr data-ip="${h.ip}" data-switch="${h.switch}" data-port="${h.port}">
                        <td style="font-family:monospace; color:var(--gold)">${hostLabel}</td>
                        <td style="font-family:monospace">${h.ip || "—"}</td>
                        <td>S${h.switch || "—"}</td>
                        <td>${h.port || "—"}</td>
                        <td style="font-family:monospace; font-size:0.75rem; color:#666">
                            ${h.mac || "—"}
                        </td>
                        <td><span class="host-type ${typeCls}">${typeLabel}</span></td>
                        <td>
                            <span class="host-status ${statusClass}">${statusText}</span>
                        </td>
                        <td>
                            <button class="btn-allow"
                                onclick="hostAction('allow', '${h.ip}', ${h.switch}, ${h.port})">
                                ✔ Permitir
                            </button>
                            <button class="btn-block-host"
                                onclick="hostAction('block', '${h.ip}', ${h.switch}, ${h.port})">
                                ✖ Bloquear
                            </button>
                        </td>
                    </tr>`;
                }).join("")}
            </tbody>
        </table>`;
}

/**
 * @async
 * @function hostAction
 * @description Despacha mandatos de control de acceso perimetral hacia las tablas de flujo OpenFlow del controlador.
 * @param {string} action Tipo de mitigación de seguridad a aplicar: `"block"` (Drop temporal) o `"allow"` (Remoción de regla).
 * @param {string} ip Dirección lógica IPv4 destino del terminal a intervenir.
 * @param {number} dpid Identificador numérico del Datapath (Switch) donde reside el enlace del host.
 * @param {number} port Puerto físico de entrada asignado en el switch OpenFlow.
 * @details Ejecuta un POST estructurado hacia el backend. En caso de éxito, realiza una mutación directa sobre 
 * el array en caché `hostsData` para evitar saltos visuales innecesarios, fuerza el redibujado inmediato de la tabla 
 * y notifica con un mensaje emergente temporal el cambio de estado del Firewall.
 */
async function hostAction(action, ip, dpid, port) {
    const endpoint = action === "block" ? "/api/host/block" : "/api/host/allow";
    try {
        await apiPost(endpoint, { ip, switch: dpid, port });
        
        // Mutación controlada e in-place del estado local
        hostsData = hostsData.map(h =>
            h.ip === ip ? { ...h, status: action === "block" ? "blocked" : "allowed" } : h
        );
        renderHostsTable();
        
        showHostMsg(action === "block"
            ? `✖ ${ip} bloqueado`
            : `✔ ${ip} permitido`,
            action === "block" ? "#DD0F50" : "#2ECC71"
        );
    } catch (e) {
        showHostMsg(`Error: ${e.message}`, "#F39C12");
    }
}

/**
 * @function showHostMsg
 * @description Expone un indicador visual transitorio de confirmación de operaciones sobre el DOM.
 * @param {string} msg Texto descriptivo de la acción completada o el error capturado.
 * @param {string} color Código hexadecimal o RGB para la estilización del texto (ej: Alerta o Éxito).
 * @details Modifica dinámicamente la opacidad de las propiedades CSS del contenedor `#hostActionMsg` 
 * e implementa un temporizador automático de limpieza de 3000ms para mitigar ruido en la interfaz.
 */
function showHostMsg(msg, color) {
    const el = document.getElementById("hostActionMsg");
    if (!el) return;
    el.textContent = msg;
    el.style.color = color;
    el.style.opacity = "1";
    setTimeout(() => { el.style.opacity = "0"; }, 3000);
}

// 12. INIT & POLLING (MOTOR ANALÍTICO Y DISPARADOR DEL CICLO DE VIDA DE LA UI)
/**
 * @function startPolling
 * @description Orquesta el ciclo de sondeo recurrente y la precarga en caliente de datos en el frontend.
 * @details Este orquestador central realiza dos etapas críticas en la inicialización del plano de gestión:
 * 1. **Disparo Inmediato (Arranque en Frío)**: Invoca de manera síncrona todas las rutinas de consulta HTTP 
 * (`pollStatus`, `pollTopology`, `pollMetrics`, etc.) antes de registrar los bucles temporales. Esto previene que la 
 * interfaz muestre estados vacíos (`empty-state`) o pantallas congeladas durante los primeros segundos tras la carga del script.
 * 2. **Hilos Temporizados Concurrentes (`setInterval`)**: Registra de forma independiente cada consumidor en el bucle 
 * de eventos de JavaScript utilizando las cadencias en milisegundos parametrizadas globalmente. Esta separación por 
 * intervalos distribuidos evita cuellos de botella en la renderización y picos de red saturados, aislando los hilos 
 * pesados (como el refresco topológico `POLL_TOPO`) de las consultas ligeras (como el latido de estado `POLL_STATUS`).
 */
function startPolling() {
    // A) PRECARGA SÍNCRONA DE LOS ENDPOINTS DEL CONTROLADOR
    pollStatus();
    pollTopology();
    pollMetrics();
    pollPredictions();
    pollCachedRoutes();
    loadPolicies();
    pollHosts();

    // B) REGISTRO Y ASIGNACIÓN DE TEMPORIZADORES DE SONDEO (POLLING LOOPS)
    setInterval(pollStatus,       POLL_STATUS);
    setInterval(pollMetrics,      POLL_METRICS);
    setInterval(pollPredictions,  POLL_ML);
    setInterval(pollTopology,     POLL_TOPO);
    setInterval(pollCachedRoutes, POLL_ROUTES);
    setInterval(pollHosts,        POLL_HOSTS);
}

/**
 * @description Listener del Evento de Ciclo de Vida del DOM.
 * @listens document:DOMContentLoaded
 * @param {Event} "DOMContentLoaded" Emitido cuando el árbol HTML estructural ha sido completamente analizado y construido por el motor del navegador.
 * @callback anonymous
 * @details Actúa como el punto de entrada raíz (`Main Entry Point`) del software del dashboard frontend. Asegura de forma determinista 
 * que los contenedores gráficos y los nodos canvas (`<canvas id="...">`) estén disponibles físicamente en el árbol del DOM antes de:
 * 1. `initCharts()`: Instanciar los motores y configurar los datasets de Chart.js.
 * 2. `initTopology()`: Configurar las fuerzas físicas de layout y las dimensiones de inyección del objeto SVG de D3.js.
 * 3. `startPolling()`: Inicializar los bucles asíncronos y poblar las estructuras de datos en vivo de la red GÉANT.
 */
document.addEventListener("DOMContentLoaded", () => {
    initCharts();
    initTopology();
    startPolling();
});