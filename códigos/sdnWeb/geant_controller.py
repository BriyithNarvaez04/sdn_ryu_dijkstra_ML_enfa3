"""
@file geant_controller.py
@brief GEANTUnifiedController — Ryu SDN Controller
@details Controlador SDN basado en Ryu que gestiona la topología GÉANT, 
integra un modelo de Machine Learning para clasificar tráfico y expone 
el estado interno mediante una API REST en Flask.
"""

import joblib
import networkx as nx
import time
import threading

import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    CONFIG_DISPATCHER,
    set_ev_cls
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, arp, ipv4

from ryu.topology import event
from ryu.topology.api import get_switch, get_link


# SHARED STATE
class SharedState:
    """
    @brief Gestor del estado compartido entre Ryu y Flask.
    @details Mantiene variables seguras para hilos (thread-safe) usando un Lock, 
    permitiendo que el subproceso de la API Flask lea datos de topología, rutas 
    y métricas mientras Ryu los actualiza.
    """

    def __init__(self):
        """
        @brief Constructor de SharedState. Inicializa estructuras de datos y políticas.
        """
        self.lock = threading.Lock()
        self.metrics = {}
        self.predictions = {}
        self.topology_nodes = []
        self.topology_edges = []
        self.hosts = {}          # mac → {ip, switch, port}
        self.routes = {}
        self.blocked_hosts = set()
        self.policy = {
            "VIDEO": {"throughput_factor": 0.6, "loss_factor": 2.5},
            "RTP":   {"throughput_factor": 0.6, "loss_factor": 2.5},
            "ICMP":  {"throughput_factor": 0.1, "loss_factor": 1.2},
            "HTTP":  {"throughput_factor": 0.0, "loss_factor": 3.0},
            "UDP":   {"base_penalty": 8.0},
            "DEFAULT": {"base_penalty": 10.0}
        }
        self.policy_enabled = True
        self.last_update = None

    def update_metric(self, key, data):
        """
        @brief Actualiza de forma segura las métricas de un puerto específico.
        @param key Tupla o string identificador (ej. `dpid:port`).
        @param data Diccionario con las métricas calculadas.
        """
        with self.lock:
            self.metrics[key] = data
            self.last_update = time.time()

    def update_prediction(self, key, data):
        """
        @brief Actualiza la predicción del modelo ML para un puerto.
        @param key Identificador del puerto.
        @param data Etiqueta de la predicción (ej. "VIDEO", "HTTP").
        """
        with self.lock:
            self.predictions[key] = data

    def update_topology(self, nodes, edges):
        """
        @brief Actualiza el estado global de la topología.
        @param nodes Lista de nodos (DPIDs de los switches).
        @param edges Lista de diccionarios representando enlaces.
        """
        with self.lock:
            self.topology_nodes = nodes
            self.topology_edges = edges

    def update_route(self, key, path):
        """
        @brief Guarda en caché una ruta calculada por Dijkstra.
        @param key String de la forma "src:dst".
        @param path Diccionario con el detalle de la ruta y sus nodos.
        """
        with self.lock:
            self.routes[key] = path

    def register_host(self, mac, ip, dpid, port):
        """
        @brief Registra un host detectado dinámicamente en la red.
        @param mac Dirección MAC del host.
        @param ip Dirección IP del host.
        @param dpid ID del switch (Datapath ID) al que está conectado.
        @param port Puerto físico del switch.
        """
        with self.lock:
            self.hosts[mac] = {
                "mac":    mac,
                "ip":     ip,
                "switch": dpid,
                "port":   port,
                "status": "blocked" if mac in self.blocked_hosts else "allowed"
            }

    def get_snapshot(self):
        """
        @brief Obtiene un volcado (snapshot) completo de todo el estado interno.
        @return Un diccionario con métricas, topología, hosts, predicciones y políticas.
        """
        with self.lock:
            return {
                "metrics":      dict(self.metrics),
                "predictions":  dict(self.predictions),
                "topology": {
                    "nodes": list(self.topology_nodes),
                    "edges": list(self.topology_edges)
                },
                "hosts":        dict(self.hosts),
                "routes":       dict(self.routes),
                "policy":       dict(self.policy),
                "policy_enabled": self.policy_enabled,
                "last_update":  self.last_update
            }


state = SharedState()

# FLASK REST API
flask_app = Flask(__name__)
CORS(flask_app)

## @brief Endpoint GET /api/topology
#  @return JSON con los nodos y enlaces (edges/links) del grafo actual.
@flask_app.route("/api/topology", methods=["GET"])
def api_topology():
    with state.lock:
        return jsonify({
            "nodes": state.topology_nodes,
            "links": state.topology_edges,   # alias para compatibilidad frontend
            "edges": state.topology_edges
        })

## @brief Endpoint GET /api/metrics
#  @return JSON con todas las métricas de todos los puertos activos.
@flask_app.route("/api/metrics", methods=["GET"])
def api_metrics():
    with state.lock:
        return jsonify(state.metrics)

## @brief Endpoint GET /api/metrics/<dpid>
#  @param dpid El ID del datapath (switch) a consultar.
#  @return JSON con las métricas filtradas exclusivamente por switch.
@flask_app.route("/api/metrics/<int:dpid>", methods=["GET"])
def api_metrics_switch(dpid):
    with state.lock:
        result = {k: v for k, v in state.metrics.items()
                  if k.startswith(f"{dpid}:")}
        return jsonify(result)

## @brief Endpoint GET /api/predictions
#  @return JSON con las clases de tráfico predichas por el modelo de ML para cada puerto.
@flask_app.route("/api/predictions", methods=["GET"])
def api_predictions():
    with state.lock:
        return jsonify(state.predictions)

## @brief Endpoint GET /api/routes
#  @return JSON con el historial de rutas precalculadas y cacheadas.
@flask_app.route("/api/routes", methods=["GET"])
def api_routes():
    with state.lock:
        return jsonify(state.routes)

## @brief Endpoint GET /api/route
#  @details Ejecuta un cálculo Dijkstra entre src y dst basado en el grafo de Ryu.
#  @return JSON detallando el camino (path) óptimo y sus pesos.
@flask_app.route("/api/route", methods=["GET"])
def api_route_query():
    src = request.args.get("src", type=int)
    dst = request.args.get("dst", type=int)
    if src is None or dst is None:
        return jsonify({"error": "src and dst required"}), 400

    ctrl = g_controller
    if ctrl is None:
        return jsonify({"error": "controller not ready"}), 503

    n_nodes = ctrl.G.number_of_nodes()
    n_edges = ctrl.G.number_of_edges()
    print(f"DIJKSTRA REQUEST src={src} dst={dst} | grafo: {n_nodes} nodos, {n_edges} edges")

    if n_edges == 0:
        return jsonify({
            "error": (
                f"Grafo sin enlaces ({n_nodes} nodos, 0 edges). "
                "Verifica que el controlador se lanzó con "
                "'ryu.topology.switches --observe-links'. "
                "Espera 20-30s para que LLDP descubra la topologia."
            )
        }), 503

    path = ctrl.get_path(src, dst)
    if not path:
        return jsonify({"error": f"No path from {src} to {dst}"}), 404

    path_detail = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        w = ctrl.G[u][v].get("weight", 1.0) if ctrl.G.has_edge(u, v) else None
        path_detail.append({"from": u, "to": v, "weight": w})

    result = {"src": src, "dst": dst, "path": path, "edges": path_detail}
    state.update_route(f"{src}:{dst}", result)
    return jsonify(result)

## @brief Endpoint GET /api/hosts
#  @details Devuelve hosts detectados por L2 learning combinados con la lista estática.
@flask_app.route("/api/hosts", methods=["GET"])
def api_hosts():
    ctrl = g_controller
    # Hosts estáticos derivados de los nodos del grafo
    static_hosts = {}
    if ctrl is not None:
        for node_id in ctrl.G.nodes():
            ip = f"10.0.{node_id}.1"
            mac_key = f"static:{node_id}"
            with state.lock:
                blocked = any(
                    h.get("ip") == ip and h.get("status") == "blocked"
                    for h in state.hosts.values()
                )
            static_hosts[ip] = {
                "mac":    None,
                "ip":     ip,
                "switch": node_id,
                "port":   1,
                "status": "blocked" if blocked else "allowed",
                "static": True
            }

    with state.lock:
        dynamic = dict(state.hosts)

    # Fusionar: los hosts dinámicos (detectados por L2) tienen prioridad
    merged = dict(static_hosts)
    for mac, h in dynamic.items():
        ip = h.get("ip")
        if ip:
            merged[ip] = {**h, "static": False}

    return jsonify(list(merged.values()))

## @brief Endpoint POST /api/host/block
#  @details Dispara la orden en Ryu para inyectar una regla de descarte (drop) para el host.
@flask_app.route("/api/host/block", methods=["POST"])
def api_host_block():
    body = request.get_json(force=True, silent=True) or {}
    ip = body.get("ip")
    dpid = body.get("switch")
    port = body.get("port")

    ctrl = g_controller
    if ctrl and dpid and port:
        ctrl.block_host(int(dpid), int(port))

    with state.lock:
        # Marcar como bloqueado
        for mac, h in state.hosts.items():
            if h.get("ip") == ip:
                h["status"] = "blocked"
                state.blocked_hosts.add(mac)

    return jsonify({"ok": True, "blocked": ip})

## @brief Endpoint POST /api/host/allow
#  @details Dispara la orden en Ryu para retirar la regla de descarte del host.
@flask_app.route("/api/host/allow", methods=["POST"])
def api_host_allow():
    body = request.get_json(force=True, silent=True) or {}
    ip = body.get("ip")
    dpid = body.get("switch")
    port = body.get("port")

    ctrl = g_controller
    if ctrl and dpid and port:
        ctrl.allow_host(int(dpid), int(port))

    with state.lock:
        for mac, h in state.hosts.items():
            if h.get("ip") == ip:
                h["status"] = "allowed"
                state.blocked_hosts.discard(mac)

    return jsonify({"ok": True, "allowed": ip})

## @brief Endpoint GET /api/policy
#  @return Políticas QoS activas de ponderación (pesos de Dijkstra).
@flask_app.route("/api/policy", methods=["GET"])
def api_policy_get():
    with state.lock:
        return jsonify({
            "policy":  state.policy,
            "enabled": state.policy_enabled
        })

## @brief Endpoint POST /api/policy
#  @details Permite la actualización en caliente de las políticas de Machine Learning.
@flask_app.route("/api/policy", methods=["POST"])
def api_policy_set():
    body = request.get_json(force=True, silent=True) or {}
    with state.lock:
        if "policy" in body:
            for traffic_type, params in body["policy"].items():
                if traffic_type in state.policy:
                    state.policy[traffic_type].update(params)
                else:
                    state.policy[traffic_type] = params
        if "enabled" in body:
            state.policy_enabled = bool(body["enabled"])
    return jsonify({"ok": True, "policy": state.policy, "enabled": state.policy_enabled})

## @brief Endpoint GET /api/status
#  @return JSON con el estado de salud, timestamp e inventario general del controlador.
@flask_app.route("/api/status", methods=["GET"])
def api_status():
    with state.lock:
        n_switches   = len(state.topology_nodes)
        n_links      = len(state.topology_edges)
        n_ports      = len(state.metrics)
        n_predictions = len(state.predictions)
        n_hosts      = len(state.hosts)
        last         = state.last_update
    return jsonify({
        "status":           "ok",
        "switches":         n_switches,
        "links":            n_links,
        "monitored_ports":  n_ports,
        "predictions":      n_predictions,
        "hosts":            n_hosts,
        "last_update":      last,
        "timestamp":        time.time()
    })

## @brief Endpoint GET /api/snapshot
#  @return Volcado global generado por `SharedState.get_snapshot()`.
@flask_app.route("/api/snapshot", methods=["GET"])
def api_snapshot():
    return jsonify(state.get_snapshot())

## @brief Endpoint GET /api/debug
#  @details Diagnóstico completo del grafo interno en Ryu, extremadamente útil para debuggear edges.
@flask_app.route("/api/debug", methods=["GET"])
def api_debug():
    """Diagnóstico completo del grafo — útil para verificar edges."""
    ctrl = g_controller
    if ctrl is None:
        return jsonify({"error": "controller not ready"}), 503
    G = ctrl.G
    nodes = sorted(G.nodes())
    edges = [
        {"src": u, "dst": v, "weight": d.get("weight", 1.0),
         "src_port": d.get("src_port"), "dst_port": d.get("dst_port")}
        for u, v, d in G.edges(data=True)
    ]
    return jsonify({
        "nodes":        nodes,
        "node_count":   len(nodes),
        "edges":        edges,
        "edge_count":   len(edges),
        "connected":    nx.is_connected(G) if len(nodes) > 0 else False,
        "datapaths":    list(ctrl.datapaths.keys()),
        "hosts_known":  len(state.hosts),
        "timestamp":    time.time()
    })


def run_flask():
    """
    @brief Ejecuta el servidor Flask. 
    @details Se llama desde un Thread separado para no bloquear el Event Loop de Ryu.
    """
    flask_app.run(host="0.0.0.0", port=8888, debug=False,
                  use_reloader=False, threaded=True)

g_controller = None

# RYU CONTROLLER
class GEANTUnifiedController(app_manager.RyuApp):
    """
    @brief Controlador principal SDN para la red GÉANT.
    @details Hereda de `app_manager.RyuApp`. Gestiona eventos OpenFlow (PacketIn, Stats),
    eventos de topología (LLDP) y ejecuta el modelo de Machine Learning.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # INIT
    def __init__(self, *args, **kwargs):
        """
        @brief Inicializa el controlador Ryu, carga el modelo ML y levanta Flask.
        """
        super(GEANTUnifiedController, self).__init__(*args, **kwargs)

        global g_controller
        g_controller = self

        self.model          = joblib.load("TrafficModel.pkl")
        self.encoder        = joblib.load("LabelEncoder.pkl")
        self.feature_columns = joblib.load("feature_columns.pkl")

        self.G           = nx.Graph()
        self.datapaths   = {}
        self.previous    = {}
        self.mac_to_port = {}        # {dpid: {mac: port}}
        self._jitter_prev = {}       # {key: prev_throughput} para estimación de jitter
        self._port_tx    = {}        # {(dpid,port): tx_pkts delta} para correlación loss
        self._port_rx    = {}        # {(dpid,port): rx_pkts delta} para correlación loss

        # Debounce para rebuild: guardamos el hub greenlet
        self._pending_rebuild = None
        self._rebuild_lock    = threading.Lock()

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("FLASK REST API en puerto 8888 (threaded)")

        self.monitor_thread = hub.spawn(self.monitor)

        print("CONTROLADOR LISTO")
        print("Clases soportadas:", self.encoder.classes_)
        print("=" * 60)
        print("IMPORTANTE: Verifica que el arranque incluye:")
        print("  ryu.topology.switches --observe-links")
        print("Sin eso, el grafo tendrá SIEMPRE 0 edges.")
        print("=" * 60)

    # SWITCH FEATURE HANDSHAKE
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        @brief Evento invocado cuando un switch completa el handshake OpenFlow.
        @details Instala flow table-miss (prioridad 0) en cada switch
        para que los paquetes desconocidos suban al controller.
        Sin esta regla, el tráfico se descarta silenciosamente.
        
        @param ev El objeto de evento que contiene el datapath.
        """
        dp      = ev.msg.datapath
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser

        # Table-miss: enviar al controller
        match   = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        inst    = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                actions)]
        mod = parser.OFPFlowMod(
            datapath=dp, priority=0,
            match=match, instructions=inst,
            idle_timeout=0, hard_timeout=0
        )
        dp.send_msg(mod)
        print(f"TABLE-MISS instalado en SW={dp.id}")

    # PACKET-IN: L2 LEARNING SWITCH
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """
        @brief Manejador de eventos PacketIn de Ryu. Actúa como L2 Learning Switch.
        @details Aprende MAC→puerto y luego instala flow rules proactivas 
        para acelerar el forwarding. Esto elimina el cuello de botella de que 
        TODO pase por el controller continuamente.
        
        @param ev Objeto EventOFPPacketIn con el paquete crudo.
        """
        msg     = ev.msg
        dp      = msg.datapath
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser
        dpid    = dp.id
        in_port = msg.match["in_port"]

        pkt     = packet.Packet(msg.data)
        eth_pkt = pkt.get_protocol(ethernet.ethernet)
        if eth_pkt is None:
            return

        dst_mac = eth_pkt.dst
        src_mac = eth_pkt.src

        # Ignorar LLDP (lo maneja Ryu internamente)
        if eth_pkt.ethertype == 0x88cc:
            return

        # Aprender MAC
        self.mac_to_port.setdefault(dpid, {})[src_mac] = in_port

        # Registrar host si tiene IP
        arp_pkt = pkt.get_protocol(arp.arp)
        ip_pkt  = pkt.get_protocol(ipv4.ipv4)
        if arp_pkt:
            state.register_host(src_mac, arp_pkt.src_ip, dpid, in_port)
        elif ip_pkt:
            state.register_host(src_mac, ip_pkt.src, dpid, in_port)

        # Determinar puerto de salida
        if dst_mac in self.mac_to_port.get(dpid, {}):
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = ofproto.OFPP_FLOOD

        # Si sabemos el puerto destino, instalar flow rule (proactivo)
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac,
                                    eth_src=src_mac)
            actions = [parser.OFPActionOutput(out_port)]
            inst    = [parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS, actions)]
            mod = parser.OFPFlowMod(
                datapath=dp, priority=1,
                idle_timeout=120, hard_timeout=0,
                match=match, instructions=inst,
                buffer_id=(msg.buffer_id if msg.buffer_id != ofproto.OFP_NO_BUFFER
                           else ofproto.OFP_NO_BUFFER)
            )
            dp.send_msg(mod)

        # Enviar paquete actual (si no lo instaló el FlowMod con buffer)
        if msg.buffer_id == ofproto.OFP_NO_BUFFER or out_port == ofproto.OFPP_FLOOD:
            actions = [parser.OFPActionOutput(out_port)]
            out = parser.OFPPacketOut(
                datapath=dp,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=actions,
                data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
            )
            dp.send_msg(out)

    # BLOQUEO / DESBLOQUEO DE HOSTS
    def block_host(self, dpid, port):
        """
        @brief Instala drop rule (regla de descarte) por OpenFlow para el puerto de un host.
        @param dpid El ID del switch.
        @param port El puerto donde está conectado el host.
        """
        if dpid not in self.datapaths:
            return
        dp      = self.datapaths[dpid]
        parser  = dp.ofproto_parser
        ofproto = dp.ofproto
        match   = parser.OFPMatch(in_port=port)
        mod = parser.OFPFlowMod(
            datapath=dp, priority=500,
            command=ofproto.OFPFC_ADD,
            idle_timeout=0, hard_timeout=0,
            match=match, instructions=[]  # DROP
        )
        dp.send_msg(mod)
        print(f"BLOQUEO: SW={dpid} puerto={port}")

    def allow_host(self, dpid, port):
        """
        @brief Elimina drop rule del puerto del host para permitir tráfico nuevamente.
        @param dpid El ID del switch.
        @param port El puerto donde está conectado el host.
        """
        if dpid not in self.datapaths:
            return
        dp      = self.datapaths[dpid]
        parser  = dp.ofproto_parser
        ofproto = dp.ofproto
        match   = parser.OFPMatch(in_port=port)
        mod = parser.OFPFlowMod(
            datapath=dp, priority=500,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match
        )
        dp.send_msg(mod)
        print(f"DESBLOQUEO: SW={dpid} puerto={port}")

    # TOPOLOGIA
    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter(self, ev):
        """
        @brief Evento invocado cuando Ryu detecta que un switch ha entrado a la red.
        @param ev Objeto de evento de topología.
        """
        dpid = ev.switch.dp.id
        if not self.G.has_node(dpid):
            self.G.add_node(dpid)
        print(f"SWITCH ENTRO: {dpid} | nodos total: {self.G.number_of_nodes()}")
        self._publish_topology()
        # Lanzar rebuild debounced: cancela el anterior y lanza uno nuevo
        self._schedule_rebuild()

    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave(self, ev):
        """
        @brief Evento invocado cuando un switch se desconecta.
        @param ev Objeto de evento de topología.
        """
        dpid = ev.switch.dp.id
        if self.G.has_node(dpid):
            self.G.remove_node(dpid)
            print(f"SWITCH SALIO: {dpid}")
        self._publish_topology()

    @set_ev_cls(event.EventLinkAdd)
    def link_add(self, ev):
        """
        @brief Captura cada enlace en tiempo real conforme LLDP los descubre.

        @details IMPORTANTE: Este evento solo se dispara si el controlador se
        lanzó con `ryu.topology.switches --observe-links`.
        
        @param ev Evento con información del enlace (origen y destino).
        """
        link     = ev.link
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid
        src_port = link.src.port_no
        dst_port = link.dst.port_no

        self.G.add_node(src_dpid)
        self.G.add_node(dst_dpid)

        if not self.G.has_edge(src_dpid, dst_dpid):
            self.G.add_edge(
                src_dpid, dst_dpid,
                src_port=src_port,
                dst_port=dst_port,
                weight=1.0
            )
            n_e = self.G.number_of_edges()
            print(f"ENLACE NUEVO: {src_dpid}<->{dst_dpid} "
                  f"p:{src_port}/{dst_port} | total edges={n_e}")
            self._publish_topology()

    @set_ev_cls(event.EventLinkDelete)
    def link_delete(self, ev):
        """
        @brief Evento invocado cuando un enlace de red desaparece o cae.
        @param ev Evento con información del enlace.
        """
        link = ev.link
        if self.G.has_edge(link.src.dpid, link.dst.dpid):
            self.G.remove_edge(link.src.dpid, link.dst.dpid)
            print(f"ENLACE ELIMINADO: {link.src.dpid}<->{link.dst.dpid}")
        self._publish_topology()

    def _schedule_rebuild(self):
        """
        @brief Debounce (limitador de rebote) para reconstruir topología.
        @details Cancela rebuild pendiente y programa uno nuevo a los 20s.
        Así si 23 switches entran en 5s, solo se hace UN rebuild al final.
        """
        with self._rebuild_lock:
            if self._pending_rebuild is not None:
                try:
                    self._pending_rebuild.kill()
                except Exception:
                    pass
            self._pending_rebuild = hub.spawn(self._delayed_topo_rebuild)

    def _delayed_topo_rebuild(self):
        """
        @brief Espera 20s para que LLDP complete el descubrimiento
        y reconstruye el grafo completo desde `get_link()`.
        """
        hub.sleep(20)
        self._full_topo_rebuild()

    def _full_topo_rebuild(self):
        """
        @brief Rebuild maestro de la topología y de los grafos NetworkX.
        @details Consulta `get_link()` y agrega edges nuevos
        preservando pesos. Solo borra edges si `get_link()` devuelve datos
        completos (al menos 1 link). Nunca destruye el grafo completo.
        """
        try:
            switches = get_switch(self, None)
            links    = get_link(self, None)

            # Agregar nodos
            for sw in switches:
                if not self.G.has_node(sw.dp.id):
                    self.G.add_node(sw.dp.id)

            if not links:
                # LLDP aún convergiendo — no tocar edges existentes
                n_n = self.G.number_of_nodes()
                n_e = self.G.number_of_edges()
                print(f"TOPO REBUILD: {n_n} nodos, {n_e} edges (links vacío — LLDP convergiendo)")
                self._publish_topology()
                return

            # Construir set de links conocidos por get_link()
            link_set = set()
            for link in links:
                src = link.src.dpid
                dst = link.dst.dpid
                k   = (min(src, dst), max(src, dst))
                link_set.add(k)

                # Agregar edge si no existe, preservando peso si ya existe
                if not self.G.has_edge(src, dst):
                    self.G.add_edge(
                        src, dst,
                        src_port=link.src.port_no,
                        dst_port=link.dst.port_no,
                        weight=1.0
                    )
                else:
                    # Actualizar puertos por si cambiaron, conservar peso
                    self.G[src][dst]["src_port"] = link.src.port_no
                    self.G[src][dst]["dst_port"] = link.dst.port_no

            # Quitar edges que get_link() ya no reporta
            for u, v in list(self.G.edges()):
                k = (min(u, v), max(u, v))
                if k not in link_set:
                    self.G.remove_edge(u, v)

            # Quitar nodos desconectados
            sw_ids = {sw.dp.id for sw in switches}
            for node in list(self.G.nodes()):
                if node not in sw_ids:
                    self.G.remove_node(node)

            n_n = self.G.number_of_nodes()
            n_e = self.G.number_of_edges()
            print(f"TOPO REBUILD COMPLETO: {n_n} nodos, {n_e} edges")

            if n_e == 0 and n_n > 0:
                print("ADVERTENCIA: 0 edges. Verifica '--observe-links' y 'ryu.topology.switches'")

            self._publish_topology()

        except Exception as e:
            print(f"ERROR _full_topo_rebuild: {e}")

    def _publish_topology(self):
        """
        @brief Publica la topología parseada hacia `SharedState`.
        @details Cada edge incluye src/dst Y source/target para compatibilidad 
        directa con D3.js en el frontend web.
        """
        nodes = list(self.G.nodes())
        edges = []
        for u, v, data in self.G.edges(data=True):
            edge = {
                "src":      u,
                "dst":      v,
                "source":   u,   # D3 usa 'source'
                "target":   v,   # D3 usa 'target'
                "weight":   data.get("weight", 1.0),
                "src_port": data.get("src_port"),
                "dst_port": data.get("dst_port")
            }
            edges.append(edge)
        state.update_topology(nodes, edges)

    # SWITCH STATE
    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER]
    )
    def state_change(self, ev):
        """
        @brief Detecta cambios de estado principal de OpenFlow en los Datapaths.
        @param ev Objeto de evento de cambio de estado.
        """
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if dp.id not in self.datapaths:
                self.datapaths[dp.id] = dp
                print(f"SWITCH CONECTADO: {dp.id} | total: {len(self.datapaths)}")
        elif ev.state == DEAD_DISPATCHER:
            if dp.id in self.datapaths:
                del self.datapaths[dp.id]
                print(f"SWITCH DESCONECTADO: {dp.id}")

    # MONITOR LOOP
    def monitor(self):
        """
        @brief Ciclo infinito corriendo en una corutina verde (hub greenlet).
        @details Pide de forma proactiva estadísticas (request_stats) a todos 
        los switches registrados en un intervalo de 15 segundos.
        """
        hub.sleep(8)   # Esperar conexión inicial
        cycle = 0

        while True:
            n_sw = len(self.datapaths)
            n_n  = self.G.number_of_nodes()
            n_e  = self.G.number_of_edges()
            print(f"MONITOR | SW={n_sw} | nodos={n_n} | edges={n_e}")

            for dp in list(self.datapaths.values()):
                self.request_stats(dp)

            # Rebuild periódico cada 60s (ciclo de 15s → cada 4 ciclos)
            cycle += 1
            if cycle % 4 == 0:
                hub.spawn(self._full_topo_rebuild)

            hub.sleep(15)

    def request_stats(self, dp):
        """
        @brief Envía una petición `OFPPortStatsRequest` al Switch.
        @param dp Objeto datapath del switch al que solicitar estadísticas.
        """
        parser = dp.ofproto_parser
        req    = parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
        dp.send_msg(req)

    # STATS + ML
    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply,
        MAIN_DISPATCHER
    )
    def port_stats(self, ev):
        dpid      = ev.msg.datapath.id
        timestamp = time.time()

        for stat in ev.msg.body:

            # Ignorar puerto LOCAL
            if stat.port_no >= 0xFFFFFF00:
                continue

            key = (dpid, stat.port_no)

            current = {
                "rx_packets":  stat.rx_packets,
                "tx_packets":  stat.tx_packets,
                "rx_bytes":    stat.rx_bytes,
                "tx_bytes":    stat.tx_bytes,
                "rx_errors":   stat.rx_errors,
                "tx_errors":   stat.tx_errors,
                # rx_dropped y tx_dropped son los contadores
                # reales de paquetes descartados en OpenFlow (OFPPortStatsReply).
                # Estos reflejan eventos reales: colas llenas, congestión,
                # buffers desbordados. NO son errores de transmisión.
                "rx_dropped":  stat.rx_dropped,
                "tx_dropped":  stat.tx_dropped,
                "timestamp":   timestamp
            }

            throughput  = 0.0
            packet_loss = 0.0

            # Métricas extendidas (SOLO para monitoreo/dashboard, NO para ML)
            pps_rx      = 0.0   # packets/sec recibidos
            pps_tx      = 0.0   # packets/sec enviados
            bytes_per_s = 0.0   # bytes/s total
            util_pct    = 0.0   # utilización estimada del enlace (%)
            jitter_est  = 0.0   # jitter estimado (ms) — variación entre-ciclos
            drop_rate   = 0.0   # paquetes descartados por segundo

            if key in self.previous:
                prev = self.previous[key]
                dt   = current["timestamp"] - prev["timestamp"]

                if dt > 0:
                    # RX+TX para capturar tráfico en cualquier dirección
                    rx_b = max(0, current["rx_bytes"]   - prev["rx_bytes"])
                    tx_b = max(0, current["tx_bytes"]   - prev["tx_bytes"])
                    rx_p = max(0, current["rx_packets"] - prev["rx_packets"])
                    tx_p = max(0, current["tx_packets"] - prev["tx_packets"])
                    throughput  = ((rx_b + tx_b) * 8) / (dt * 1_000_000)
                    bytes_per_s = (rx_b + tx_b) / dt
                    pps_rx      = rx_p / dt
                    pps_tx      = tx_p / dt

                    # PACKET LOSS REAL
                    rx_drop = max(0, current["rx_dropped"] - prev["rx_dropped"])
                    tx_drop = max(0, current["tx_dropped"] - prev["tx_dropped"])
                    total_drop = rx_drop + tx_drop

                    # Guardar TX de este puerto para correlación cruzada
                    self._port_tx[key] = tx_p

                    # Buscar el puerto vecino que debería recibir lo que mandamos
                    neighbor_key = self._get_neighbor_port(dpid, stat.port_no)
                    if neighbor_key and neighbor_key in self._port_tx:
                        neighbor_rx = self._port_rx.get(neighbor_key, 0)
                        if tx_p > 0:
                            lost = max(0, tx_p - neighbor_rx)
                            packet_loss = min((lost / tx_p) * 100, 100.0)
                    elif total_drop > 0:
                        # Fallback: drops reportados por OVS (raro pero posible)
                        total_pkts = rx_p + tx_p
                        if total_pkts > 0:
                            packet_loss = min(
                                (total_drop / (total_pkts + total_drop)) * 100,
                                100.0
                            )
                        else:
                            packet_loss = 100.0

                    # Guardar RX de este puerto para que el vecino lo use
                    self._port_rx[key] = rx_p

                    drop_rate = total_drop / dt

                    # Utilización: throughput actual vs capacidad nominal 1 Gbps
                    # (en Mininet los enlaces son 1 Gbps por defecto)
                    LINK_CAPACITY_MBPS = 1000.0
                    util_pct = min((throughput / LINK_CAPACITY_MBPS) * 100, 100.0)

                    # Jitter estimado: variación de bytes/s entre ciclos consecutivos
                    # Usamos diferencia absoluta normalizada como proxy de jitter
                    prev_thr_key = f"_thr_{key[0]}_{key[1]}"
                    prev_thr = self._jitter_prev.get(prev_thr_key, throughput)
                    jitter_est = abs(throughput - prev_thr) * 10  # escala a ms aprox
                    self._jitter_prev[prev_thr_key] = throughput

            # SIEMPRE guardar previous
            self.previous[key] = current

            # Features para ML — SIN CAMBIOS (no modificar)
            features_dict = {
                "switch":          int(dpid),
                "port":            int(stat.port_no),
                "rx_packets":      float(current["rx_packets"]),
                "tx_packets":      float(current["tx_packets"]),
                "rx_bytes":        float(current["rx_bytes"]),
                "tx_bytes":        float(current["tx_bytes"]),
                "throughput_mbps": float(throughput),
                "packet_loss":     float(packet_loss)
            }

            try:
                features = pd.DataFrame([features_dict])[self.feature_columns]
            except Exception as e:
                print("ERROR FEATURES:", e)
                continue

            # Predicción ML — sin cambios
            try:
                pred         = self.model.predict(features)[0]
                traffic_type = self.encoder.inverse_transform([pred])[0]
            except Exception as e:
                print("ERROR PREDICCION:", e)
                continue

            weight = self.compute_weight(throughput, packet_loss, traffic_type)
            self.update_link(dpid, stat.port_no, weight)

            metric_key = f"{dpid}:{stat.port_no}"

            state.update_metric(metric_key, {
                # Core (usados por ML y Dijkstra)
                "switch":          dpid,
                "port":            stat.port_no,
                "rx_packets":      current["rx_packets"],
                "tx_packets":      current["tx_packets"],
                "rx_bytes":        current["rx_bytes"],
                "tx_bytes":        current["tx_bytes"],
                "throughput_mbps": round(throughput, 4),
                "packet_loss":     round(packet_loss, 4),
                "traffic_type":    traffic_type,
                "weight":          round(weight, 2),
                "timestamp":       timestamp,
                # Extendidas (SOLO visualización/dashboard)
                "rx_dropped":      current["rx_dropped"],
                "tx_dropped":      current["tx_dropped"],
                "rx_errors":       current["rx_errors"],
                "tx_errors":       current["tx_errors"],
                "pps_rx":          round(pps_rx, 2),
                "pps_tx":          round(pps_tx, 2),
                "bytes_per_s":     round(bytes_per_s, 2),
                "utilization_pct": round(util_pct, 4),
                "jitter_est_ms":   round(jitter_est, 4),
                "drop_rate_pps":   round(drop_rate, 4),
            })

            state.update_prediction(metric_key, {
                "switch":          dpid,
                "port":            stat.port_no,
                "traffic_type":    traffic_type,
                "weight":          round(weight, 2),
                "throughput_mbps": round(throughput, 4),
                "packet_loss":     round(packet_loss, 4),
                "timestamp":       timestamp
            })

            if throughput > 0 or packet_loss > 0:
                print(
                    f"[SW={dpid} P={stat.port_no}] "
                    f"{traffic_type} "
                    f"THR={throughput:.4f}Mbps "
                    f"LOSS={packet_loss:.2f}% W={weight:.2f} "
                    f"RXb={current['rx_bytes']} TXb={current['tx_bytes']}"
                )

    # NEIGHBOR PORT LOOKUP (para correlación packet loss)
    def _get_neighbor_port(self, dpid, port_no):
        """
        Dado un (dpid, port_no) de salida, devuelve el (dpid_vecino, puerto_entrada)
        usando la información de los edges del grafo (src_port / dst_port).
        Necesario para correlacionar TX de este puerto con RX del vecino.
        """
        for u, v, data in self.G.edges(data=True):
            if u == dpid and data.get("src_port") == port_no:
                return (v, data.get("dst_port"))
            if v == dpid and data.get("dst_port") == port_no:
                return (u, data.get("src_port"))
        return None

    # PESO DINÁMICO
    def compute_weight(self, throughput, loss, traffic_type):
        w = 1.0
        if not state.policy_enabled:
            return w
        with state.lock:
            p = state.policy
        if traffic_type in ["VIDEO", "RTP"]:
            tf = p.get("VIDEO", {}).get("throughput_factor", 0.6)
            lf = p.get("VIDEO", {}).get("loss_factor", 2.5)
            w += ((100 - throughput) * tf) + (loss * lf)
        elif traffic_type == "ICMP":
            lf = p.get("ICMP", {}).get("loss_factor", 1.2)
            tf = p.get("ICMP", {}).get("throughput_factor", 0.1)
            w += (loss * lf) + (throughput * tf)
        elif traffic_type == "HTTP":
            lf = p.get("HTTP", {}).get("loss_factor", 3.0)
            w += loss * lf
        elif traffic_type == "UDP":
            bp = p.get("UDP", {}).get("base_penalty", 8.0)
            w += bp
        else:
            bp = p.get("DEFAULT", {}).get("base_penalty", 10.0)
            w += bp
        return max(w, 1.0)

    # UPDATE LINK
    def update_link(self, node, port, weight):
        for u, v, data in self.G.edges(data=True):
            if u == node and data.get("src_port") == port:
                self.G[u][v]["weight"] = weight
            elif v == node and data.get("dst_port") == port:
                self.G[v][u]["weight"] = weight

    # DIJKSTRA
    def get_path(self, src, dst):
        n_n = self.G.number_of_nodes()
        n_e = self.G.number_of_edges()

        if n_n == 0:
            print("ERROR DIJKSTRA: grafo vacio")
            return []
        if n_e == 0:
            print(f"ERROR DIJKSTRA: 0 edges ({n_n} nodos). "
                  "¿Lanzaste con ryu.topology.switches --observe-links?")
            return []
        if src not in self.G:
            print(f"ERROR DIJKSTRA: src={src} no en grafo. Nodos: {sorted(self.G.nodes())}")
            return []
        if dst not in self.G:
            print(f"ERROR DIJKSTRA: dst={dst} no en grafo. Nodos: {sorted(self.G.nodes())}")
            return []

        try:
            path = nx.dijkstra_path(self.G, src, dst, weight="weight")
            print(f"DIJKSTRA OK: {src}->{dst} = {path}")
            return path
        except nx.NetworkXNoPath:
            print(f"ERROR DIJKSTRA: sin camino {src}->{dst}")
            return []
        except Exception as e:
            print(f"ERROR DIJKSTRA: {e}")
            return []