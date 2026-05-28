/**
 * @file server.js
 * @brief Backend Node.js para el SDN GÉANT Dashboard.
 * @details Backend Node.js/Express que actúa como proxy inverso entre el dashboard web 
 * y la API Flask del controlador. Sirve los archivos estáticos del frontend, 
 * expone los endpoints `/api/*` al navegador y evita problemas de CORS.
 */

const express = require("express");
const cors    = require("cors");
const axios   = require("axios");
const path    = require("path");

const app = express();
app.use(cors());
app.use(express.json());

// Servir frontend desde carpeta public/ (o el mismo directorio)
app.use(express.static(path.join(__dirname, "public")));
app.use(express.static(__dirname));  // fallback para index.html en raíz

// CONFIG

/** @brief URL base de la API REST de OpenFlow nativa de Ryu. */
const RYU_OPENFLOW = process.env.RYU_IP   || "http://<IP-VM2>:8080";

/** @brief URL base de la API Flask embebida en el controlador Ryu. */
const RYU_FLASK    = process.env.FLASK_IP || "http://<IP-VM2>:8888";

/** @brief Puerto de escucha del servidor Node.js. */
const PORT         = process.env.PORT     || 3000;

// HELPERS

/**
 * @brief Realiza una petición GET a la API Flask de Ryu.
 * @param p La ruta del endpoint (ej. "/api/topology").
 * @return Promesa con los datos JSON devueltos por el controlador.
 */
async function ryuGet(p) {
    const response = await axios.get(`${RYU_FLASK}${p}`, { timeout: 15000 });
    return response.data;
}

/**
 * @brief Realiza una petición POST a la API Flask de Ryu.
 * @param p La ruta del endpoint (ej. "/api/host/block").
 * @param body El cuerpo de la petición en formato JSON.
 * @return Promesa con los datos JSON devueltos por el controlador.
 */
async function ryuPost(p, body) {
    const response = await axios.post(`${RYU_FLASK}${p}`, body, {
        timeout: 15000,
        headers: { "Content-Type": "application/json" }
    });
    return response.data;
}

// SDN API PROXY ROUTES

/**
 * @brief Endpoint GET /api/topology
 * @details Retorna el grafo completo de la topología GÉANT (nodos y enlaces).
 */
app.get("/api/topology", async (req, res) => {
    try {
        const data = await ryuGet("/api/topology");
        res.json(data);
    } catch (e) {
        console.error("/api/topology error:", e.message);
        res.status(503).json({ error: "Ryu not reachable", detail: e.message });
    }
});

/**
 * @brief Endpoint GET /api/metrics
 * @details Obtiene las métricas actuales de todos los puertos de todos los switches.
 */
app.get("/api/metrics", async (req, res) => {
    try {
        const data = await ryuGet("/api/metrics");
        res.json(data);
    } catch (e) {
        console.error("/api/metrics error:", e.message);
        res.status(503).json({ error: "Ryu not reachable", detail: e.message });
    }
});

/**
 * @brief Endpoint GET /api/metrics/:dpid
 * @details Obtiene las métricas filtradas por un switch específico (datapath ID).
 */
app.get("/api/metrics/:dpid", async (req, res) => {
    try {
        const data = await ryuGet(`/api/metrics/${req.params.dpid}`);
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint GET /api/predictions
 * @details Devuelve las predicciones activas del modelo de Machine Learning.
 */
app.get("/api/predictions", async (req, res) => {
    try {
        const data = await ryuGet("/api/predictions");
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint GET /api/routes
 * @details Devuelve las rutas Dijkstra precalculadas y cacheadas por el controlador.
 */
app.get("/api/routes", async (req, res) => {
    try {
        const data = await ryuGet("/api/routes");
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint GET /api/route
 * @details Calcula y devuelve la ruta óptima entre dos switches utilizando Dijkstra con pesos dinámicos.
 * @param src ID del switch de origen (enviado como query param).
 * @param dst ID del switch de destino (enviado como query param).
 */
app.get("/api/route", async (req, res) => {
    const { src, dst } = req.query;
    if (!src || !dst) {
        return res.status(400).json({ error: "src and dst required" });
    }
    try {
        const data = await ryuGet(`/api/route?src=${src}&dst=${dst}`);
        res.json(data);
    } catch (e) {
        const status = e.response ? e.response.status : 503;
        const body   = e.response ? e.response.data   : { error: e.message };
        res.status(status).json(body);
    }
});

// HOSTS
/**
 * @brief Endpoint GET /api/hosts
 * @details Lista los hosts detectados dinámicamente por el controlador.
 */
app.get("/api/hosts", async (req, res) => {
    try {
        const data = await ryuGet("/api/hosts");
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint POST /api/host/block
 * @details Instala una regla OpenFlow para bloquear el tráfico de un host específico.
 */
app.post("/api/host/block", async (req, res) => {
    try {
        const data = await ryuPost("/api/host/block", req.body);
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint POST /api/host/allow
 * @details Elimina la regla de bloqueo OpenFlow para permitir el tráfico de un host.
 */
app.post("/api/host/allow", async (req, res) => {
    try {
        const data = await ryuPost("/api/host/allow", req.body);
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

// DEBUG

/**
 * @brief Endpoint GET /api/debug
 * @details Retorna el diagnóstico del grafo interno: nodos, edges y estado de conectividad.
 */
app.get("/api/debug", async (req, res) => {
    try {
        const data = await ryuGet("/api/debug");
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint GET /api/policy
 * @details Obtiene las políticas QoS actuales (factores de peso) por clase de tráfico.
 */
app.get("/api/policy", async (req, res) => {
    try {
        const data = await ryuGet("/api/policy");
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint POST /api/policy
 * @details Actualiza dinámicamente las políticas QoS por clase de tráfico.
 */
app.post("/api/policy", async (req, res) => {
    try {
        const data = await ryuPost("/api/policy", req.body);
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

/**
 * @brief Endpoint GET /api/status
 * @details Muestra el estado del controlador: número de switches, enlaces y timestamp.
 */
app.get("/api/status", async (req, res) => {
    try {
        const data = await ryuGet("/api/status");
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: "Controller offline", detail: e.message });
    }
});

/**
 * @brief Endpoint GET /api/snapshot
 * @details Devuelve un snapshot completo del estado interno del controlador.
 */
app.get("/api/snapshot", async (req, res) => {
    try {
        const data = await ryuGet("/api/snapshot");
        res.json(data);
    } catch (e) {
        res.status(503).json({ error: e.message });
    }
});

// LEGACY ENDPOINTS

/**
 * @brief Endpoint POST /flow
 * @details Agrega una entrada de flujo directamente vía OpenFlow. (Endpoint heredado/Legacy).
 */
app.post("/flow", async (req, res) => {
    try {
        const response = await axios.post(
            `${RYU_OPENFLOW}/stats/flowentry/add`,
            req.body
        );
        res.json(response.data);
    } catch (error) {
        console.error(error.message);
        res.status(500).send("Error");
    }
});

/**
 * @brief Endpoint GET /bw
 * @details Calcula la utilización del ancho de banda total por los switches principales (Heredado/Legacy).
 */
app.get("/bw", async (req, res) => {
    try {
        const metrics = await ryuGet("/api/metrics");
        const entries = Object.values(metrics);
        const bySwitch = {};
        for (const m of entries) {
            const sw = m.switch;
            if (!bySwitch[sw]) bySwitch[sw] = 0;
            bySwitch[sw] += m.throughput_mbps || 0;
        }
        res.json({
            h1_h4: (bySwitch[1] || 0).toFixed(2),
            h2_h4: (bySwitch[2] || 0).toFixed(2),
            h3_h4: (bySwitch[3] || 0).toFixed(2)
        });
    } catch (err) {
        console.error("Error BW:", err.message);
        res.status(500).json({ error: "Controller not reachable" });
    }
});

// START
app.listen(PORT, "0.0.0.0", () => {
    console.log(`✔ Backend corriendo en http://localhost:${PORT}`);
    console.log(`✔ Proxying Ryu Flask API en ${RYU_FLASK}`);
    console.log(`Ryu OpenFlow REST en ${RYU_OPENFLOW}`);
    console.log(`\nEndpoints disponibles:`);
    console.log(`  GET  /api/topology    → topología (nodes + edges/links)`);
    console.log(`  GET  /api/hosts       → hosts detectados dinámicamente`);
    console.log(`  POST /api/host/block  → bloquear host`);
    console.log(`  POST /api/host/allow  → permitir host`);
    console.log(`  GET  /api/debug       → diagnóstico del grafo`);
    console.log(`  GET  /api/route?src=N&dst=M → Dijkstra`);
});