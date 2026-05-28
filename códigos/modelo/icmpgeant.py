#!/usr/bin/env python3
"""
@file icmpgeant.py
@brief Script de Mininet para emular tráfico de control y diagnóstico (ICMP) en la topología GÉANT.
@details Construye la red académica GÉANT y genera múltiples ráfagas de paquetes ping (ICMP) entre 
nodos distantes. A diferencia de un flujo continuo (como VIDEO o GAMING), el tráfico ICMP se modela 
mediante ráfagas cortas y agresivas separadas por silencios (pausas). Esto genera el patrón 
característico de ICMP: picos (spikes) de throughput en ventanas temporales separadas, resultando 
en una alta variabilidad (burstiness) capturada por el monitor SDN.
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.topo import Topo

import subprocess
import time

# CONFIGURACIÓN GENERAL
## @var RYU_IP
#  @brief Dirección IP del controlador SDN Ryu remoto.
RYU_IP   = "<IP-VM2>"

## @var RYU_PORT
#  @brief Puerto TCP de escucha para el protocolo OpenFlow en Ryu.
RYU_PORT = 6653

## @var NUM_SAMPLES
#  @brief Número total de flujos de prueba (muestras) a generar en la simulación.
NUM_SAMPLES   = 32

# PARÁMETROS DE PING (PERFIL ICMP)

## @var PING_COUNT
#  @brief Número de paquetes enviados por cada ráfaga.
#  @details Se utiliza un valor pequeño (20 paquetes) para garantizar que toda la ráfaga 
#  quepa dentro de 1 sola ventana temporal del monitor SDN (20 × 0.1s = 2s activos).
PING_COUNT       = 20     

## @var PING_INTERVAL
#  @brief Intervalo en segundos entre cada paquete ping.
#  @details Configurado agresivamente a 100ms (0.1s) para concentrar el tráfico en un 
#  pico claro de throughput.
PING_INTERVAL    = 0.1    

## @var PING_SIZE
#  @brief Tamaño del payload del paquete ping en bytes.
#  @details Se fuerza un tamaño grande (1400B) para que los bytes sean estadísticamente 
#  visibles por el monitor y superen el ruido del tráfico de control OpenFlow.
PING_SIZE        = 1400   

## @var NUM_BURSTS
#  @brief Número de ráfagas consecutivas que componen una única muestra (sample).
NUM_BURSTS       = 6      

## @var BURST_PAUSE
#  @brief Tiempo de pausa (silencio) en segundos entre ráfagas.
#  @details Al establecer 10s, se garantizan al menos 2 ventanas completamente ociosas (idle) 
#  en el monitor SDN (que lee cada 5s), separando los spikes correctamente.
BURST_PAUSE      = 10
## @var SAMPLE_WAIT
#  @brief Cálculo del tiempo total que tardará en ejecutarse una muestra completa.
#  @details 6 ráfagas × (2s activo + 10s pausa) + 5s = ~77s. Genera ~15 ventanas en el monitor.
SAMPLE_WAIT      = NUM_BURSTS * (PING_COUNT * PING_INTERVAL + BURST_PAUSE) + 5
print(f"  Duración estimada por muestra: {SAMPLE_WAIT:.0f}s")

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
    @details Define 23 nodos (switches) interconectados replicando el backbone europeo, 
    conectando un host dedicado a cada switch para inyectar el tráfico.
    """
    def build(self):
        """
        @brief Instancia los switches, hosts y enlaces de la topología.
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
        print(
            f"  ICMP {NUM_BURSTS} ráfagas × "
            f"{PING_COUNT} pings × {PING_SIZE}B "
            f"cada {PING_INTERVAL}s | pausa {BURST_PAUSE}s entre ráfagas\n"
        )

        for b in range(NUM_BURSTS):

            print(f"    ráfaga {b+1}/{NUM_BURSTS} ...")
            # lanzar ping en background y esperar su duración
            # -c  número de paquetes por ráfaga
            # -i  intervalo agresivo (100ms) para generar spike claro
            # -s  payload grande → bytes visibles en el monitor
            #     (ping normal de 60B sería invisible vs tráfico de control)
            src.cmd(
                f"ping -c {PING_COUNT} "
                f"-i {PING_INTERVAL} "
                f"-s {PING_SIZE} "
                f"{dst.IP()} "
                f"> /dev/null 2>&1"  # síncrono — espera fin de ráfaga
            )

            # NOTA: el comando es síncrono (sin &) — Mininet espera
            # a que termine el ping antes de continuar.
            # Esto asegura que la pausa empieza exactamente después
            # de la última respuesta, no antes.
            if b < NUM_BURSTS - 1:
                # pausa entre ráfagas — el monitor captura ventanas idle
                # que alternan con las ventanas de spike → burstiness alto
                print(f"    pausa {BURST_PAUSE}s ...")
                time.sleep(BURST_PAUSE)

        src.cmd("pkill -f ping")
        # esperar a que el monitor capture la ventana final post-muestra
        time.sleep(10)

    net.stop()

if __name__ == "__main__":
    main()