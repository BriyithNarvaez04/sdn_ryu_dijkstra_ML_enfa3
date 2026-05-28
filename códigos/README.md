# Instrucciones para documentación Doxygen

En la carpeta `modelo` se encuentran los códigos que generan las clases de tráfico especificadas: VIDEO, HTTP, GAMING e ICMP, además del script utilizado para recolectar los datos necesarios para entrenar el modelo. Dentro de la subcarpeta `datasets` están los archivos CSV con los datos correspondientes a cada clase.

Se incluye una imagen que muestra la importancia de cada variable resultante del entrenamiento: `images/importancia_variables.png` (o la ruta correspondiente en el proyecto).

En la carpeta `html` del módulo `modelo` se encuentra el archivo `index.html`, que ofrece la interfaz web con la documentación generada para los archivos relacionados con `modelo`.

En la carpeta `sdnWeb` están los códigos que conforman la interfaz web del proyecto, incluyendo el controlador `geant_controller.py`. Asimismo, `sdnWeb` contiene su propia carpeta `html` donde puede accederse al archivo `index.html` para visualizar la documentación de sus archivos.
