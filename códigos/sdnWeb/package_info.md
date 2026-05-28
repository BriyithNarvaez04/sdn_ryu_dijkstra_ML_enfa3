/**
 * @page dependencias Dependencias y Configuración (Node.js)
 * @tableofcontents
 * * @section package_json Archivo package.json
 * Este archivo define la configuración del servidor backend proxy para el Dashboard de SDN GÉANT.
 * * @subsection scripts Scripts Disponibles
 * - `npm start`: Inicia el servidor de producción usando Node (`node server.js`).
 * - `npm run dev`: Inicia el servidor en modo desarrollo usando Nodemon (se reinicia automáticamente si hay cambios).
 * * @subsection dependencies Dependencias Principales
 * El proyecto utiliza las siguientes librerías core (ver `package.json`):
 * - **express** (^4.18.2): Framework web para crear la API proxy y servir los archivos estáticos del frontend.
 * - **axios** (^1.6.0): Cliente HTTP basado en promesas usado para hacer las peticiones (polling) hacia la API Flask de Ryu.
 * - **cors** (^2.8.5): Middleware para habilitar el intercambio de recursos de origen cruzado, permitiendo que el navegador consulte la API sin bloqueos de seguridad.
 * * @subsection dev_dependencies Dependencias de Desarrollo
 * - **nodemon** (^3.0.0): Utilidad que monitorea los cambios en el código fuente y reinicia automáticamente el servidor.
 * * @section package_lock Archivo package-lock.json
 * Este archivo es autogenerado por npm. Bloquea las versiones exactas de las dependencias (y sus sub-dependencias, como `accepts`, `agent-base`, etc.) para asegurar que el proyecto se ejecute exactamente igual en la máquina virtual 1, la máquina virtual 2 o cualquier otro entorno de despliegue. **No debe modificarse manualmente.**
 */