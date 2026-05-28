"""
@file monitoring.py
@brief Aplicación Ryu para monitorización de tráfico y recolección de datasets.
@details Este módulo implementa un controlador SDN (basado en Ryu) que actúa de forma pasiva. 
Consulta periódicamente las estadísticas de los puertos de todos los switches (OFPPortStatsRequest) 
y calcula métricas derivadas complejas como throughput, jitter, burstiness, y asimetría de red. 
Los datos generados se exportan a un archivo CSV que sirve como "dataset crudo" para el 
posterior entrenamiento del modelo de Machine Learning.
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls
)

from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

import csv
import time
import os
import math

# CONFIGURACIÓN
## @var MONITOR_INTERVAL
#  @brief Segundos entre cada ventana de muestreo.
#  @details Con 5s: ICMP (~2s por ráfaga) genera spikes visibles en ventanas separadas; 
#  duplica la densidad temporal del dataset vs 10s.
MONITOR_INTERVAL = 5     

## @var CSV_FILE
#  @brief Nombre del archivo de salida donde se añadirán las métricas.
CSV_FILE         = "ryu_metrics.csv"

## @var LINK_BW_MBPS
#  @brief Ancho de banda nominal de los enlaces en megabits por segundo.
#  @details Debe coincidir con LINK_BW en los scripts de generación de tráfico de Mininet.
LINK_BW_MBPS     = 100   

## @var JITTER_WINDOW
#  @brief Número de ventanas anteriores para calcular jitter y burstiness por puerto.
#  @details Al usar 4, cubre 4 × 5s = 20s de historial.
JITTER_WINDOW    = 4     

class SDNMonitor(app_manager.RyuApp):
    """
    @brief Clase principal del monitor SDN de recolección de datos.
    @details Hereda de `app_manager.RyuApp`. Mantiene el estado de los switches conectados, 
    gestiona el historial temporal de throughput para el cálculo de varianza y coordina la 
    escritura asíncrona de los datos calculados al archivo CSV.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # INIT
    def __init__(self, *args, **kwargs):
        """
        @brief Constructor del monitor SDN.
        @details Inicializa las estructuras de memoria, crea el hilo (greenlet) de 
        monitorización en bucle y prepara el archivo CSV.
        """
        super(SDNMonitor, self).__init__(*args, **kwargs)

        self.datapaths   = {}
        self.previous    = {}

        # historial de throughput por puerto para jitter y burstiness
        # {(dpid, port_no): [thr_t-3, thr_t-2, thr_t-1, thr_t]}
        self.thr_history = {}

        self.monitor_thread = hub.spawn(self.monitor)
        self.init_csv()

        print("\n=== RYU MONITOR v2 STARTED ===\n")
        print(f"    Intervalo : {MONITOR_INTERVAL}s")
        print(f"    Link BW   : {LINK_BW_MBPS} Mbps")
        print(f"    Jitter win: {JITTER_WINDOW} ventanas\n")

    # CSV
    def init_csv(self):
        """
        @brief Inicializa el archivo CSV.
        @details Si el archivo definido en `CSV_FILE` no existe, lo crea e inyecta 
        automáticamente la fila de cabeceras (headers) con los nombres de las métricas.
        """
        if not os.path.exists(CSV_FILE):

            with open(CSV_FILE, 'w', newline='') as f:

                writer = csv.writer(f)

                writer.writerow([
                    # identificadores
                    'timestamp',
                    'switch',
                    'port',
                    # contadores acumulados OVS
                    'rx_packets',
                    'tx_packets',
                    'rx_bytes',
                    'tx_bytes',
                    # throughput
                    'throughput_mbps',      # tx Mbps en la ventana
                    'rx_throughput_mbps',   # rx Mbps — distingue flujos uni/bidireccionales
                    'utilization',          # throughput / LINK_BW [0,1]
                    # asimetría de puerto
                    'port_asymmetry',       # desbalance rx/tx (%)
                    # tamaño y cadencia de paquetes
                    'bytes_per_packet',     # tamaño medio paquete TX (B)
                    'packet_rate',          # paquetes/s TX
                    # variabilidad temporal
                    'jitter_mbps',          # std del throughput en las últimas N ventanas
                    'burstiness',           # CV = std/mean del throughput
                    # ratio rx/tx
                    'rx_tx_ratio',          # rx_bytes / tx_bytes
                    # errores de enlace
                    'tx_error_rate',        # tx_errors / tx_packets
                    'rx_error_rate',        # rx_errors / rx_packets
                    # flag de actividad
                    'idle_flag',            # 1 si thr < 0.01 Mbps
                ])

    # SWITCH EVENTS
    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER]
    )
    def state_change_handler(self, ev):
        """
        @brief Registra la conexión o desconexión de los switches al controlador.
        @param ev Objeto de evento de cambio de estado.
        """
        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.datapaths[datapath.id] = datapath
                print(f"Switch conectado: {datapath.id}")

        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                del self.datapaths[datapath.id]

    # MONITOR LOOP
    def monitor(self):
        """
        @brief Bucle principal de recolección de estadísticas.
        @details Corutina que se ejecuta infinitamente. Itera sobre todos los switches 
        conectados y les solicita sus estadísticas de puerto cada `MONITOR_INTERVAL` segundos.
        """
        while True:
            for dp in list(self.datapaths.values()):
                self.request_stats(dp)
            hub.sleep(MONITOR_INTERVAL)

    # REQUEST PORT STATS
    def request_stats(self, datapath):
        """
        @brief Solicita las estadísticas de los puertos a un switch particular.
        @param datapath Objeto que representa la conexión OpenFlow con el switch.
        """
        parser = datapath.ofproto_parser

        req = parser.OFPPortStatsRequest(
            datapath,
            0,
            datapath.ofproto.OFPP_ANY
        )

        datapath.send_msg(req)

    # PORT STATS REPLY
    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply,
        MAIN_DISPATCHER
    )
    def port_stats_reply_handler(self, ev):
        """
        @brief Procesa la respuesta de estadísticas (PortStatsReply) enviada por el switch.
        @details Cruza los datos actuales con los almacenados en la ventana anterior (`self.previous`)
        para calcular eltas (diferencias de tiempo, bytes y paquetes). Luego, calcula las variables 
        derivadas como el throughput en Mbps, la varianza temporal (jitter) y añade la fila final al archivo CSV.
        
        @param ev Evento de respuesta OpenFlow que contiene los contadores en bruto.
        """
        body      = ev.msg.body
        dpid      = ev.msg.datapath.id
        timestamp = time.time()

        for stat in body:

            # ignorar puerto LOCAL de OVS (0xFFFFFFFE)
            if stat.port_no == 4294967294:
                continue

            key = (dpid, stat.port_no)

            current = {
                'rx_packets': stat.rx_packets,
                'tx_packets': stat.tx_packets,
                'rx_bytes':   stat.rx_bytes,
                'tx_bytes':   stat.tx_bytes,
                'tx_errors':  stat.tx_errors,
                'rx_errors':  stat.rx_errors,
                'timestamp':  timestamp,
            }

            # inicializar valores por defecto
            throughput_mbps    = 0.0
            rx_throughput_mbps = 0.0
            utilization        = 0.0
            port_asymmetry     = 0.0
            bytes_per_packet   = 0.0
            packet_rate        = 0.0
            jitter_mbps        = 0.0
            burstiness         = 0.0
            rx_tx_ratio        = 0.0
            tx_error_rate      = 0.0
            rx_error_rate      = 0.0
            idle_flag          = 1   # idle hasta demostrar actividad

            # inicializar historial si es la primera vez
            if key not in self.thr_history:
                self.thr_history[key] = []

            # cálculos diferenciales
            if key in self.previous:

                prev = self.previous[key]
                dt   = current['timestamp'] - prev['timestamp']

                if dt > 0:

                    tx_bytes_diff   = current['tx_bytes']   - prev['tx_bytes']
                    rx_bytes_diff   = current['rx_bytes']   - prev['rx_bytes']
                    tx_packets_diff = current['tx_packets'] - prev['tx_packets']
                    rx_packets_diff = current['rx_packets'] - prev['rx_packets']
                    tx_errors_diff  = current['tx_errors']  - prev['tx_errors']
                    rx_errors_diff  = current['rx_errors']  - prev['rx_errors']

                    # descartar si hay reset de contadores OVS
                    if (tx_bytes_diff   < 0 or rx_bytes_diff   < 0 or
                            tx_packets_diff < 0 or rx_packets_diff < 0):
                        print(
                            f"[SW={dpid} PORT={stat.port_no}] "
                            f"RESET detectado — muestra descartada"
                        )
                        self.previous[key] = current
                        continue

                    # throughput TX
                    throughput_mbps = (
                        (tx_bytes_diff * 8) / (dt * 1_000_000)
                    )

                    # throughput RX
                    rx_throughput_mbps = (
                        (rx_bytes_diff * 8) / (dt * 1_000_000)
                    )

                    # utilización [0, 1]
                    utilization = min(
                        throughput_mbps / LINK_BW_MBPS, 1.0
                    )

                    # port_asymmetry
                    if tx_packets_diff >= 10:
                        ratio = rx_packets_diff / tx_packets_diff
                        if ratio < 0.05:
                            # UDP unidireccional — no es pérdida real
                            port_asymmetry = 0.0
                        else:
                            port_asymmetry = max(
                                0.0,
                                (tx_packets_diff - rx_packets_diff)
                                / tx_packets_diff * 100
                            )
                    else:
                        # ventana idle — menos de 10 paquetes
                        port_asymmetry = 0.0

                    # bytes_per_packet
                    bytes_per_packet = (
                        tx_bytes_diff / tx_packets_diff
                        if tx_packets_diff > 0 else 0.0
                    )

                    # packet_rate (pps TX)
                    packet_rate = tx_packets_diff / dt

                    # rx_tx_ratio
                    # cap en 2.0 — valores mayores son topología,
                    # no característica de la clase de tráfico
                    rx_tx_ratio = min(
                        rx_bytes_diff / tx_bytes_diff
                        if tx_bytes_diff > 0 else 0.0,
                        2.0
                    )

                    # error rates
                    tx_error_rate = (
                        tx_errors_diff / tx_packets_diff
                        if tx_packets_diff > 0 else 0.0
                    )
                    rx_error_rate = (
                        rx_errors_diff / rx_packets_diff
                        if rx_packets_diff > 0 else 0.0
                    )

                    # idle_flag
                    idle_flag = 1 if throughput_mbps < 0.01 else 0

                    # actualizar historial de throughput
                    hist = self.thr_history[key]
                    hist.append(throughput_mbps)
                    if len(hist) > JITTER_WINDOW:
                        hist.pop(0)

                    # jitter y burstiness
                    # requiere al menos 2 muestras en historial
                    if len(hist) >= 2:
                        mean_thr = sum(hist) / len(hist)
                        variance = sum(
                            (x - mean_thr) ** 2 for x in hist
                        ) / len(hist)
                        jitter_mbps = math.sqrt(variance)
                        burstiness  = (
                            jitter_mbps / mean_thr
                            if mean_thr > 0 else 0.0
                        )

            self.previous[key] = current

            print(
                f"[SW={dpid:2d}] [P={stat.port_no:2d}] "
                f"TX={throughput_mbps:.3f}Mbps "
                f"RX={rx_throughput_mbps:.3f}Mbps "
                f"UTL={utilization:.3f} "
                f"ASYM={port_asymmetry:.1f}% "
                f"BPP={bytes_per_packet:.0f}B "
                f"PPS={packet_rate:.0f} "
                f"JIT={jitter_mbps:.3f} "
                f"BURST={burstiness:.2f} "
                f"IDLE={idle_flag}"
            )

            with open(CSV_FILE, 'a', newline='') as f:
                writer = csv.writer(f)

                writer.writerow([
                    round(timestamp,          3),
                    dpid,
                    stat.port_no,
                    current['rx_packets'],
                    current['tx_packets'],
                    current['rx_bytes'],
                    current['tx_bytes'],
                    round(throughput_mbps,    6),
                    round(rx_throughput_mbps, 6),
                    round(utilization,        6),
                    round(port_asymmetry,     4),
                    round(bytes_per_packet,   2),
                    round(packet_rate,        4),
                    round(jitter_mbps,        6),
                    round(burstiness,         6),
                    round(rx_tx_ratio,        6),
                    round(tx_error_rate,      6),
                    round(rx_error_rate,      6),
                    idle_flag,
                ])