#!/usr/bin/env python3
"""
@file entrenamiento.py
@brief Script de entrenamiento del modelo Machine Learning (RandomForest) para SDN GÉANT.
@details Lee el dataset procesado (`dataset_balanced.csv`), aplica transformaciones de datos, 
entrena un clasificador RandomForest para categorizar el tráfico de red y exporta los artefactos necesarios 
(`TrafficModel.pkl`, `LabelEncoder.pkl`, `feature_columns.pkl`) para el controlador Ryu.

@note Clases clasificadas: VIDEO, HTTP, GAMING, ICMP.
"""

import pandas as pd
import numpy as np
import joblib
import matplotlib
matplotlib.use('Agg')   # sin display — guarda figura a disco
import matplotlib.pyplot as plt

from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

# CONFIGURACIÓN
## @var CSV_FILE
#  @brief Ruta del archivo de entrada que contiene el dataset balanceado.
CSV_FILE      = "dataset_balanced.csv"

## @var MODEL_FILE
#  @brief Archivo de salida donde se guardará el modelo entrenado (RandomForest).
MODEL_FILE    = "TrafficModel.pkl"

## @var ENCODER_FILE
#  @brief Archivo de salida para el codificador de etiquetas (transforma strings a enteros).
ENCODER_FILE  = "LabelEncoder.pkl"

## @var FEATURES_FILE
#  @brief Archivo de salida que guarda el orden exacto de las columnas usadas para entrenar.
FEATURES_FILE = "feature_columns.pkl"

## @var NON_FEATURE_COLS
#  @brief Lista de columnas crudas que NO se usarán como features de entrenamiento.
#  @details 
#  Se excluyen por tres motivos principales:
#  1. Identificadores de topología (switch, port): el modelo debe aprender el perfil del tráfico, no qué switch lo genera.
#  2. Contadores acumulados (rx_packets, tx_bytes): originan features derivadas (como throughput), pero no son features estables en sí mismos.
#  3. Metadatos del proceso de captura (idle_flag, timestamp, traffic_type): etiquetas internas y variable objetivo que no representan características de red.
NON_FEATURE_COLS = [
    'timestamp',
    'switch',
    'port',
    'rx_packets',
    'tx_packets',
    'rx_bytes',
    'tx_bytes',
    'idle_flag',       # FIX: meta-dato de captura, no feature de tráfico
    'traffic_type',
]

# 1. CARGA
print("\n" + "="*55)
print(" PASO 1 — CARGA")
print("="*55)

df = pd.read_csv(CSV_FILE)
print(f"  Filas: {len(df):,}  |  Columnas: {df.shape[1]}")
print(f"  Columnas: {df.columns.tolist()}")

# 2. LIMPIEZA
print("\n" + "="*55)
print(" PASO 2 — LIMPIEZA")
print("="*55)

antes = len(df)
df = df.dropna(subset=["traffic_type"])
print(f"  Filas sin etiqueta eliminadas: {antes - len(df)}")

df["traffic_type"] = (
    df["traffic_type"]
    .astype(str)
    .str.strip()
    .str.upper()
)

print(f"\n  Distribución de clases:")
print(df["traffic_type"].value_counts().to_string())

# 3. FEATURES Y TARGET
print("\n" + "="*55)
print(" PASO 3 — FEATURES Y TARGET")
print("="*55)

cols_to_drop = [c for c in NON_FEATURE_COLS if c in df.columns]
X = df.drop(columns=cols_to_drop)
y = df["traffic_type"]

print(f"  Features usados ({len(X.columns)}):")
for col in X.columns:
    print(f"    - {col}")

nan_counts = X.isnull().sum()
if nan_counts.any():
    print(f"\n  NaN por columna:")
    print(nan_counts[nan_counts > 0].to_string())
    X = X.fillna(0.0)
    print(f"  → rellenados con 0.0")

le_y = LabelEncoder()
y_encoded = le_y.fit_transform(y)

print(f"\n  Codificación de clases:")
for label, code in zip(le_y.classes_, le_y.transform(le_y.classes_)):
    print(f"    {label} → {code}")

# 4. TRAIN / TEST SPLIT
print("\n" + "="*55)
print(" PASO 4 — TRAIN / TEST SPLIT (80/20)")
print("="*55)

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y_encoded,
    test_size=0.2,
    random_state=42,
    stratify=y_encoded
)

print(f"  Train: {len(X_train):,} filas")
print(f"  Test : {len(X_test):,} filas")

# 5. MODELO
print("\n" + "="*55)
print(" PASO 5 — ENTRENAMIENTO")
print("="*55)

model = RandomForestClassifier(
    n_estimators=200,
    max_depth=10,
    min_samples_leaf=5,
    random_state=42,
    n_jobs=-1
)

model.fit(X_train, y_train)
print("  Modelo entrenado.")

# 6. EVALUACIÓN
print("\n" + "="*55)
print(" PASO 6 — EVALUACIÓN")
print("="*55)

y_pred = model.predict(X_test)
acc    = accuracy_score(y_test, y_pred)

print(f"\n  Accuracy: {acc:.4f} ({acc*100:.2f}%)")
print(f"\n  Classification Report:\n")
print(classification_report(
    y_test, y_pred,
    target_names=le_y.classes_
))

cm    = confusion_matrix(y_test, y_pred)
cm_df = pd.DataFrame(cm, index=le_y.classes_, columns=le_y.classes_)
print("  Matriz de confusión:")
print(cm_df.to_string())

# 7. VALIDACIÓN CRUZADA
print("\n" + "="*55)
print(" PASO 7 — VALIDACIÓN CRUZADA (5 folds)")
print("="*55)

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
scores = cross_val_score(model, X_train, y_train, cv=cv, scoring='accuracy')

print(f"\n  Scores por fold: {[f'{s:.4f}' for s in scores]}")
print(f"  Media  : {scores.mean():.4f}")
print(f"  Std    : {scores.std():.4f}")

if scores.std() > 0.05:
    print(f"  ⚠️  Alta varianza entre folds — posible sobreajuste o dataset pequeño")

# 8. IMPORTANCIA DE VARIABLES
print("\n" + "="*55)
print(" PASO 8 — IMPORTANCIA DE VARIABLES")
print("="*55)

importances = pd.Series(model.feature_importances_, index=X.columns)
importances = importances.sort_values(ascending=False)

print()
for feat, imp in importances.items():
    bar = '█' * int(imp * 50)
    print(f"  {feat:25s} {imp:.4f}  {bar}")

importances.sort_values(ascending=True).plot(kind="barh", figsize=(10, 6))
plt.title("Importancia de Variables — RandomForest")
plt.tight_layout()
plt.savefig("feature_importance.png", dpi=150)
plt.close()
print(f"\n  Figura guardada en: feature_importance.png")

# 9. EXPORTAR ARTEFACTOS
print("\n" + "="*55)
print(" PASO 9 — EXPORTAR ARTEFACTOS")
print("="*55)

joblib.dump(model,           MODEL_FILE)
joblib.dump(le_y,            ENCODER_FILE)
joblib.dump(list(X.columns), FEATURES_FILE)

print(f"  ✅ {MODEL_FILE}")
print(f"  ✅ {ENCODER_FILE}")
print(f"  ✅ {FEATURES_FILE}")
print(f"     Features exportadas: {list(X.columns)}")
print("="*55 + "\n")