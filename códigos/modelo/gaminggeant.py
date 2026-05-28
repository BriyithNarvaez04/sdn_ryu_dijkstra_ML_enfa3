#!/usr/bin/env python3
"""
@file gaminggeant.py
@brief Script de Mininet para emular tráfico de videojuegos (GAMING) en la topología GÉANT.
@details Construye la topología de la red académica europea GÉANT utilizando Mininet, 
conecta los switches a un controlador SDN remoto y genera flujos de tráfico UDP continuos.
Simula el comportamiento de un servidor de videojuegos multijugador enviando paquetes pequeños 
a una tasa de bits constante. Estos flujos son monitoreados pasivamente por Ryu para crear 
el dataset de entrenamiento ML.
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.topo import Topo

import subprocess
import time

# =====================================================
# CONFIGURACIÓN DEL PERFIL DE TRÁFICO (GAMING)
# =====================================================

## @var RYU_IP
#  @brief Dirección IP del controlador SDN Ryu remoto.
RYU_IP   = "<IP-VM2>"

## @var RYU_PORT
#  @brief Puerto TCP de escucha para el protocolo OpenFlow en Ryu.
RYU_PORT = 6653

## @var NUM_SAMPLES
#  @brief Número total de flujos de prueba (muestras) a generar en la simulación.
NUM_SAMPLES     = 32

## @var STREAM_PORT
#  @brief Puerto de destino utilizado por la herramienta iperf3 para recibir los datos.
STREAM_PORT     = 5201

## @var IPERF_BW
#  @brief Tasa de bits de la simulación.
#  @details 2M simula el tráfico UDP de juegos reales de disparos/acción (ej. CS:GO ~2M, Valorant ~3M).
IPERF_BW        = "2M"     

## @var IPERF_PKT
#  @brief Tamaño de carga útil del paquete en bytes.
#  @details Es un diferenciador clave para el Machine Learning. En videojuegos, los paquetes 
#  son muy pequeños (100-300 bytes) porque solo transmiten el estado/posición del jugador.
IPERF_PKT       = 200      

## @var STREAM_DURATION
#  @brief Duración activa de cada flujo en segundos.
#  @details Ajustado a 60s para asegurar múltiples capturas del monitor (que lee cada 5s).
STREAM_DURATION = 60       

## @var STP_WAIT
#  @brief Tiempo de convergencia inicial en segundos para el protocolo Spanning Tree.
STP_WAIT = 30

## @var LINK_BW
#  @brief Capacidad nominal de cada enlace simulado (Mbps).
LINK_BW = 100

# TOPOLOGÍA GEANT
class GeantTopo(Topo):
    """
    @brief Clase constructora de la topología GÉANT en Mininet.
    @details Define 23 nodos (switches) interconectados de acuerdo a la infraestructura 
    real del backbone europeo, conectando un host dedicado a cada switch.
    """

    def build(self):
        """
        @brief Instancia los switches, hosts y enlaces con sus respectivos anchos de banda.
        """
        switches = {}

        for i in range(1, 24):
            switches[i] = self.addSwitch(f's{i}')

        for i in range(1, 24):
            h = self.addHost(f'h{i}')
            self.addLink(h, switches[i], bw=LINK_BW)

        links = [
            (1,2),(1,3),(1,4),
            (2,5),(2,6),
            (3,6),(3,7),
            (4,7),(4,8),

            (1,5),(1,6),(1,7),(1,8),
            (2,7),(2,8),
            (3,5),(3,8),
            (4,5),(4,6),

            (5,9),(5,10),
            (6,10),(6,11),
            (7,11),(7,12),
            (8,12),(8,13),

            (5,6),(6,7),(7,8),
            (9,10),(10,11),(11,12),(12,13),

            (9,14),(10,14),(10,15),
            (11,15),(11,16),
            (12,16),(12,17),
            (13,17),(13,18),

            (9,15),(10,16),(11,17),(12,18),

            (14,19),
            (15,19),(15,20),
            (16,20),(16,21),
            (17,21),(17,22),
            (18,22),(18,23),

            (14,15),(15,16),(16,17),(17,18),

            (19,20),
            (21,22),
            (22,23),

            (19,21),(20,22),(21,23),(19,22),
            (20,23)
        ]

        for a, b in links:
            self.addLink(switches[a], switches[b], bw=LINK_BW)

# LIMPIEZA
def clean_mininet():
    """
    @brief Limpia procesos residuales y configuraciones previas de Mininet.
    @details Ejecuta `mn -c` a nivel de sistema operativo para asegurar un inicio limpio 
    sin colisiones de puertos o switches virtuales colgados.
    """
    subprocess.call("sudo mn -c", shell=True)
    time.sleep(3)

# MAIN
def main():
    """
    @brief Bloque principal de ejecución.
    @details Arranca Mininet, asocia el controlador remoto, espera la convergencia de la red 
    y ejecuta los comandos `iperf3` en pares de hosts distantes simulando sesiones de juego UDP.
    """
    setLogLevel("info")

    clean_mininet()

    net = Mininet(
        topo=GeantTopo(),
        controller=None,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True
    )

    net.addController('c0', RemoteController, ip=RYU_IP, port=RYU_PORT)
    net.start()

    print("\n=== ENABLING STP ===\n")
    for sw in net.switches:
        sw.cmd(f"ovs-vsctl set Bridge {sw.name} stp_enable=true")

    print(f"=== WAITING {STP_WAIT}s FOR STP CONVERGENCE ===\n")
    time.sleep(STP_WAIT)

    hosts = sorted(net.hosts, key=lambda h: int(h.name[1:]))

    distant_pairs = [
        (hosts[i], hosts[-(i + 1)])
        for i in range(len(hosts))
    ]

    pairs = (
        distant_pairs * (NUM_SAMPLES // len(distant_pairs) + 1)
    )[:NUM_SAMPLES]

    for i, (src, dst) in enumerate(pairs):

        print(f"\n=== SAMPLE {i+1}/{NUM_SAMPLES} ===")
        print(f"  {src.name}({src.IP()}) --> {dst.name}({dst.IP()})")
        print(f"  GAMING/UDP {IPERF_BW} pkt={IPERF_PKT}B durante {STREAM_DURATION}s\n")

        src.cmd("pkill -f iperf3")
        dst.cmd("pkill -f iperf3")
        time.sleep(1)

        dst.cmd(
            f"iperf3 -s -p {STREAM_PORT} "
            f"> /dev/null 2>&1 &"
        )
        time.sleep(2)

        # -u    UDP — gaming prioriza latencia sobre fiabilidad
        # -b    bitrate constante — simula tick rate del servidor de juego
        # -t    duración — flujo continuo
        # -l    tamaño de paquete pequeño — diferenciador clave vs VIDEO (1316B)
        src.cmd(
            f"iperf3 -c {dst.IP()} "
            f"-u "
            f"-b {IPERF_BW} "
            f"-t {STREAM_DURATION} "
            f"-l {IPERF_PKT} "
            f"-p {STREAM_PORT} "
            f"> /dev/null 2>&1 &"
        )

        time.sleep(STREAM_DURATION + 5)

        src.cmd("pkill -f iperf3")
        dst.cmd("pkill -f iperf3")
        time.sleep(10)

    net.stop()

if __name__ == "__main__":
    main()