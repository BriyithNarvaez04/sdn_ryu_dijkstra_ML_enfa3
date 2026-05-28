#!/usr/bin/env python3
"""
@file build_dataset.py
@brief Une los CSV individuales por clase, limpia ruido y balancea el dataset final.
@details Script de preprocesamiento que consolida las métricas generadas por el monitor SDN.
Aplica filtros estrictos para solucionar artefactos de Open vSwitch (OVS) y Mininet, como 
el desbordamiento de contadores (overflow uint64), asimetrías falsas y tráfico de control subyacente.
Finalmente, aplica un undersampling para exportar un dataset equilibrado (`dataset_balanced.csv`) 
listo para entrenar el modelo RandomForest.

@note Uso: `python3 build_dataset.py`
"""

import pandas as pd
import numpy as np
import os

# CONFIGURACIÓN

## @var INPUT_FILES
#  @brief Diccionario que mapea los archivos CSV de entrada con su etiqueta de clase ML.
INPUT_FILES = {
    'video_metrics.csv':  'VIDEO',
    'http_metrics.csv':   'HTTP',
    'gaming_metrics.csv': 'GAMING',
    'icmp_metrics.csv':   'ICMP',
}

## @var OUTPUT_FILE
#  @brief Nombre del archivo de salida para el dataset consolidado.
OUTPUT_FILE  = 'dataset_balanced.csv'

## @var RANDOM_STATE
#  @brief Semilla de aleatoriedad para asegurar la reproducibilidad del undersampling.
RANDOM_STATE = 42

## @var THR_MIN
#  @brief Umbral mínimo de throughput (Mbps) para descartar tráfico de control OpenFlow.
#  @details El tráfico de control suele flotar en ~0.0007 Mbps. Se usa 0.01 como corte seguro.
THR_MIN = 0.01  # Mbps

## @var ASYMMETRY_MAX
#  @brief Asimetría máxima permitida (%) en flujos de tráfico.
#  @details Valores > 20% en UDP suelen ser artefactos de puertos unidireccionales.
ASYMMETRY_MAX = 20.0  # %

## @var BPP_CAP
#  @brief Cap físico de bytes por paquete (MTU de Ethernet).
#  @details Ningún paquete Ethernet supera este MTU (1500 B). Valores mayores son 
#  artefactos estadísticos de acumulación en ventanas largas.
BPP_CAP = 1500.0  # bytes

## @var EXPECTED_RANGES
#  @brief Diccionario informativo con el rango esperado de throughput (Mbps) por clase.
EXPECTED_RANGES = {
    'VIDEO':  (2.0,  15.0),
    'HTTP':   (10.0, 100.0),
    'GAMING': (1.0,  5.0),
    'ICMP':   (0.0,  0.5),
}

## @var NEW_COLUMNS
#  @brief Lista de columnas añadidas en el monitoring v2.
#  @details Útil para mantener compatibilidad con datasets generados por la versión v1 del monitor.
NEW_COLUMNS = [
    'rx_throughput_mbps',
    'utilization',
    'jitter_mbps',
    'burstiness',
    'rx_tx_ratio',
    'tx_error_rate',
    'rx_error_rate',
    'idle_flag',
]

# CARGA

def load_and_label(filepath: str, label: str) -> pd.DataFrame:
    """
    @brief Carga un archivo CSV y le añade la columna de etiqueta (target).
    @param filepath Ruta al archivo CSV.
    @param label Nombre de la clase de tráfico (ej. 'VIDEO', 'HTTP').
    @return DataFrame con los datos cargados y la etiqueta asignada.
    """
    df = pd.read_csv(filepath)
    df['traffic_type'] = label
    print(f"  [{label}] cargado: {len(df):,} filas  |  "
          f"columnas: {df.shape[1]}")
    return df

# LIMPIEZA

def remove_counter_overflow(df: pd.DataFrame) -> pd.DataFrame:
    """
    @brief Descarta filas con contadores de red desbordados (overflow de uint64).
    @details OVS puede presentar dos tipos de overflow:
    1. **Clásico (negativo)**: Ocasionalmente el contador de bytes disminuye erróneamente.
    2. **Absurdo positivo**: El contador hace wrap-around produciendo diferencias gigantescas,
       generando métricas derivadas imposibles (ej. throughput > 1000 Mbps en enlaces de 100 Mbps).
    
    @param df DataFrame a procesar.
    @return DataFrame sin los registros corruptos por desbordamiento.
    """
    # Filtro 1 — contadores acumulados fuera de rango físico
    mask_counters = (
        (df["rx_packets"] > 1e15) |
        (df["tx_packets"] > 1e15) |
        (df["rx_bytes"]   > 1e15) |
        (df["tx_bytes"]   > 1e15)
    )

    # Filtro 2 — valores derivados físicamente imposibles
    # throughput > 1000 Mbps: imposible con LINK_BW=100
    # packet_rate > 1e6 pkt/s: imposible en interfaces virtuales Mininet
    # bytes_per_packet > 65535: imposible (límite máximo IP)
    mask_absurd = (
        (df["throughput_mbps"]  > 1000)  |
        (df["packet_rate"]      > 1e6)   |
        (df["bytes_per_packet"] > 65535)
    )

    mask    = mask_counters | mask_absurd
    dropped = mask.sum()
    if dropped:
        c = mask_counters.sum()
        a = mask_absurd.sum()
        print(f"    → overflow de contadores (clásico={c}, absurdo={a}): "
              f"{dropped} filas eliminadas")
    return df[~mask].copy()

def remove_control_traffic(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    @brief Descarta filas compuestas únicamente por tráfico de control OpenFlow.
    @details Si se dispone del `idle_flag` (monitoring v2), filtra usando esa marca de forma uniforme 
    para todas las clases. Si no (monitoring v1), usa un umbral manual basado en `THR_MIN`.
    
    @param df DataFrame a limpiar.
    @param label Clase actual (usado para aplicar umbrales especiales a ICMP en v1).
    @return DataFrame libre de ventanas ociosas.
    """
    if 'idle_flag' in df.columns:
        # monitoring v2 — idle_flag filtra todas las clases uniformemente
        mask   = df['idle_flag'] == 1
        source = 'idle_flag'
    else:
        # monitoring v1 — umbral manual diferenciado
        if label == 'ICMP':
            mask = df['throughput_mbps'] == 0.0
        else:
            mask = df['throughput_mbps'] < THR_MIN
        source = 'umbral manual'

    dropped = mask.sum()
    if dropped:
        print(f"    → tráfico de control OpenFlow ({source}): "
              f"{dropped} filas eliminadas")
    return df[~mask].copy()

def remove_false_asymmetry(df: pd.DataFrame) -> pd.DataFrame:
    """
    @brief Descarta asimetrías falsas (artefactos UDP/unidireccionales).
    @details Borra filas que superan `ASYMMETRY_MAX` si su throughput es mayor a 0.1 Mbps, 
    atrapando puertos que fallan en reportar el tráfico de retorno real.
    
    @param df DataFrame a filtrar.
    @return DataFrame corregido.
    """
    col = 'port_asymmetry' if 'port_asymmetry' in df.columns else 'packet_loss'

    # FIX: umbral 0.1 en lugar de 1.0 — cubre GAMING, ICMP y VIDEO bajo
    mask = (df['throughput_mbps'] > 0.1) & (df[col] > ASYMMETRY_MAX)
    dropped = mask.sum()
    if dropped:
        print(f"    → falsos {col} > {ASYMMETRY_MAX}% (thr > 0.1 Mbps): "
              f"{dropped} filas eliminadas")
    return df[~mask].copy()

def fix_bytes_per_packet(df: pd.DataFrame) -> pd.DataFrame:
    """
    @brief Corrige anomalías en la métrica de bytes por paquete.
    @details Realiza dos procesos: limita físicamente el valor a `BPP_CAP` (MTU) y 
    fuerza el valor a 0 en las ventanas marcadas como residualmente ociosas (`idle_flag`).
    
    @param df DataFrame a corregir.
    @return DataFrame con BPP estandarizado.
    """
    if 'bytes_per_packet' not in df.columns:
        return df

    # Cap en MTU
    before_cap = (df['bytes_per_packet'] > BPP_CAP).sum()
    df['bytes_per_packet'] = df['bytes_per_packet'].clip(upper=BPP_CAP)
    if before_cap:
        print(f"    → bytes_per_packet > {BPP_CAP}B (> MTU): "
              f"{before_cap} filas capadas a {BPP_CAP}B")

    # Cero en ventanas idle residuales (solo monitoring v2)
    if 'idle_flag' in df.columns:
        idle_bpp = (df['idle_flag'] == 1).sum()
        if idle_bpp:
            df.loc[df['idle_flag'] == 1, 'bytes_per_packet'] = 0.0
            print(f"    → bytes_per_packet en ventanas idle residuales: "
                  f"{idle_bpp} filas → 0.0")

    return df

def validate_range(df: pd.DataFrame, label: str) -> None:
    """
    @brief Valida e imprime en consola si el throughput actual coincide con los rangos esperados.
    @details Función meramente informativa (no muta el DataFrame) para diagnosticar la salud 
    del tráfico inyectado durante la toma de datos.
    
    @param df DataFrame limpio de la clase actual.
    @param label Nombre de la clase.
    """
    lo, hi = EXPECTED_RANGES[label]
    in_range = df[
        (df['throughput_mbps'] >= lo) &
        (df['throughput_mbps'] <= hi)
    ]
    pct = len(in_range) / len(df) * 100 if len(df) else 0
    print(f"    → en rango esperado ({lo}–{hi} Mbps): "
          f"{len(in_range):,} filas ({pct:.1f}%)")
    print(f"    → throughput : "
          f"media={df['throughput_mbps'].mean():.4f}  "
          f"p50={df['throughput_mbps'].median():.4f}  "
          f"max={df['throughput_mbps'].max():.4f}")

    col = 'port_asymmetry' if 'port_asymmetry' in df.columns else 'packet_loss'
    print(f"    → {col}: "
          f"media={df[col].mean():.2f}%  "
          f"max={df[col].max():.2f}%")

    if 'burstiness' in df.columns:
        print(f"    → burstiness: "
              f"media={df['burstiness'].mean():.3f}  "
              f"p95={df['burstiness'].quantile(0.95):.3f}")

def clean(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    @brief Función orquestadora que aplica todo el pipeline de limpieza a un DataFrame.
    @param df DataFrame crudo.
    @param label Clase de tráfico.
    @return DataFrame completamente depurado.
    """
    print(f"\n  Limpiando [{label}]...")
    df = remove_counter_overflow(df)
    df = remove_control_traffic(df, label)
    df = remove_false_asymmetry(df)
    df = fix_bytes_per_packet(df)
    validate_range(df, label)
    print(f"    → filas limpias: {len(df):,}")
    return df

# SELECCIÓN DE FEATURES

def select_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    @brief Selecciona las características (features) finales que consumirá el modelo ML.
    @details Garantiza compatibilidad retroactiva revisando si el DataFrame posee las columnas 
    nuevas generadas en `monitoring_v2.py`.
    
    @param df DataFrame unificado y balanceado.
    @return DataFrame estructurado con únicamente las columnas predictoras útiles.
    """
    core = [
        'throughput_mbps',
        'bytes_per_packet',
        'packet_rate',
    ]

    # port_asymmetry (v2) o packet_loss (v1)
    if 'port_asymmetry' in df.columns:
        core.append('port_asymmetry')
    elif 'packet_loss' in df.columns:
        core.append('packet_loss')

    available_new = [col for col in NEW_COLUMNS if col in df.columns]
    missing_new   = [col for col in NEW_COLUMNS if col not in df.columns]

    if missing_new:
        print(f"\n  ⚠️  Columnas de monitoring v2 ausentes: {missing_new}")
        print(f"      Regenerar el CSV con monitoring_v2.py")

    features = core + available_new + ['traffic_type']
    return df[features].copy()

# MAIN

def main():
    """
    @brief Punto de entrada del script.
    @details 
    1. Carga los CSV de entrada y los limpia.
    2. Aplica Undersampling aleatorio al mínimo común de muestras para balancear las clases.
    3. Mezcla aleatoriamente (shuffle) el resultado y exporta `dataset_balanced.csv`.
    """
    print("\n" + "="*55)
    print(" PASO 1 — CARGA Y LIMPIEZA POR CLASE")
    print("="*55)

    cleaned = {}
    missing = []

    for filename, label in INPUT_FILES.items():
        if not os.path.exists(filename):
            print(f"\n  ⚠️  [{label}] archivo no encontrado: {filename}")
            missing.append(label)
            continue
        df = load_and_label(filename, label)
        df = clean(df, label)
        cleaned[label] = df

    if missing:
        print(f"\n  Clases faltantes: {missing}")
        print("  Se continuará solo con las clases disponibles.\n")

    if not cleaned:
        print("\nError: no hay archivos CSV disponibles.")
        return

    print("\n" + "="*55)
    print(" PASO 2 — BALANCEO")
    print("="*55)

    counts = {label: len(df) for label, df in cleaned.items()}
    min_count = min(counts.values())

    print(f"\n  Filas por clase antes de balancear:")
    for label, count in counts.items():
        print(f"    {label:8s}: {count:,}")
    print(f"\n  Mínimo común: {min_count:,} filas por clase")

    if min_count < 100:
        print(f"\n  ⚠️  Mínimo muy bajo ({min_count} filas) — "
              f"considerar regenerar el dataset de esa clase")

    balanced_parts = []
    for label, df in cleaned.items():
        sample = df.sample(min_count, random_state=RANDOM_STATE)
        balanced_parts.append(sample)
        print(f"  [{label}] → {min_count:,} filas seleccionadas")

    print("\n" + "="*55)
    print(" PASO 3 — SELECCIÓN DE FEATURES Y EXPORTACIÓN")
    print("="*55)

    dataset = pd.concat(balanced_parts, ignore_index=True)
    dataset = dataset.sample(frac=1, random_state=RANDOM_STATE).reset_index(drop=True)
    dataset = select_features(dataset)

    dataset.to_csv(OUTPUT_FILE, index=False)

    print(f"\n  Dataset final   : {len(dataset):,} filas × {dataset.shape[1]} columnas")
    print(f"  Features usados : {[c for c in dataset.columns if c != 'traffic_type']}")
    print(f"  Clases          : {sorted(dataset['traffic_type'].unique())}")
    print(f"\n  Distribución final:")
    print(dataset['traffic_type'].value_counts().to_string())
    print(f"\n  ✅ Guardado en: {OUTPUT_FILE}")
    print("="*55 + "\n")


if __name__ == "__main__":
    main()