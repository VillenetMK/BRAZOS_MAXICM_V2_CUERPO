import json
import os
import time
from flask import Flask, jsonify, render_template_string, request
import serial

app = Flask(__name__)

# Configuración del Puerto Serie
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200

try:
    esp32 = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)
    print(f"Conectado exitosamente al ESP32 en {SERIAL_PORT}")
except Exception as e:
    print(
        f"Advertencia: No se pudo conectar al ESP32 ({e}). Modo simulación activado."
    )
    esp32 = None

# Configuración y restricciones físicas de los servos
SERVOS_CONFIG = {
    0: {
        "name": "Brazo 1 IZQ Hombro",
        "min": 0,
        "max": 145,
        "current": 135,
        "home": 135,
        "interval": 5,
    },
    1: {
        "name": "Brazo 1 IZQ Codo Rot",
        "min": 0,
        "max": 200,
        "current": 110,
        "home": 110,
        "interval": 5,
    },
    2: {
        "name": "Brazo 1 IZQ Codo Vert",
        "min": 0,
        "max": 75,
        "current": 75,
        "home": 75,
        "interval": 5,
    },
    4: {
        "name": "Brazo 2 DER Hombro",
        "min": 125,
        "max": 270,
        "current": 135,
        "home": 135,
        "interval": 5,
    },
    5: {
        "name": "Brazo 2 DER Codo Rot",
        "min": 0,
        "max": 150,
        "current": 50,
        "home": 50,
        "interval": 5,
    },
    6: {
        "name": "Brazo 2 DER Codo Vert",
        "min": 0,
        "max": 270,
        "current": 0,
        "home": 0,
        "interval": 5,
    },
}

STEPS_FILE = "pasos_individuales.json"


def load_steps():
    if os.path.exists(STEPS_FILE):
        with open(STEPS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_steps(steps):
    with open(STEPS_FILE, "w") as f:
        json.dump(steps, f, indent=4)


def enviar_comando(comando):
    if esp32 and esp32.is_open:
        esp32.write(f"{comando}\n".encode())
        print(f"Enviado a ESP32: {comando}")
    else:
        print(f"[Simulación] Comando: {comando}")


def calcular_movimiento_suave(channel, nuevo_angulo, custom_interval=None):
    config = SERVOS_CONFIG[channel]
    angulo_actual = config["current"]
    intervalo = (
        custom_interval if custom_interval is not None else config["interval"]
    )

    pulsos_actual = 100 + int((angulo_actual * 420) / 270)
    pulsos_nuevo = 100 + int((nuevo_angulo * 420) / 270)
    diferencia_pulsos = abs(pulsos_nuevo - pulsos_actual)

    tiempo_estimado_ms = diferencia_pulsos * intervalo
    return tiempo_estimado_ms / 1000.0


def ejecutar_paso_individual(step_data):
    """Ejecuta el movimiento de un solo motor con su respectiva velocidad"""
    channel = int(step_data["channel"])
    target_angle = int(step_data["angle"])
    speed_interval = int(step_data.get("interval", 5))

    # 1. Ajustar la velocidad de ese canal específico en el ESP32
    enviar_comando(f"V {channel} {speed_interval}")
    SERVOS_CONFIG[channel]["interval"] = speed_interval

    # 2. Calcular tiempo antes de actualizar la posición actual en Python
    tiempo_viaje = calcular_movimiento_suave(channel, target_angle, speed_interval)

    # 3. Enviar comando de movimiento e internalizar posición
    SERVOS_CONFIG[channel]["current"] = target_angle
    enviar_comando(f"{channel} {target_angle}")

    return tiempo_viaje


@app.route("/")
def index():
    return render_template_string(HTML_INTERFACE)


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify({"servos": SERVOS_CONFIG, "steps": load_steps()})


@app.route("/api/move", methods=["POST"])
def move_servo():
    data = request.json
    channel = int(data["channel"])
    action = data["action"]

    config = SERVOS_CONFIG[channel]
    target_angle = config["current"]

    if action == "step_up":
        target_angle = min(config["max"], config["current"] + 1)
    elif action == "step_down":
        target_angle = max(config["min"], config["current"] - 1)
    elif action == "angle":
        target_angle = max(config["min"], min(config["max"], int(data["value"])))

    tiempo_segundos = calcular_movimiento_suave(channel, target_angle)
    config["current"] = target_angle
    enviar_comando(f"{channel} {target_angle}")

    return jsonify(
        {
            "status": "success",
            "current_angle": target_angle,
            "estimated_time_seconds": round(tiempo_segundos, 3),
        }
    )


# Enviar a Home todos los motores de golpe
@app.route("/api/home", methods=["POST"])
def go_home():
    comando_bulk = ""
    for ch, config in SERVOS_CONFIG.items():
        enviar_comando(f"V {ch} 5")  # Velocidad estándar para ir a Home
        config["interval"] = 5
        config["current"] = config["home"]
        comando_bulk += f"{ch} {config['home']} "

    enviar_comando(comando_bulk.strip())
    return jsonify({"status": "success"})


# Guardar un paso individual
@app.route("/api/steps", methods=["POST"])
def save_step():
    data = request.json
    step_name = data["name"]
    channel = int(data["channel"])
    angle = int(data["angle"])
    interval = int(data.get("interval", 5))
    delay = float(data.get("delay", 0.5))

    steps = load_steps()
    steps[step_name] = {
        "channel": channel,
        "angle": angle,
        "interval": interval,
        "delay": delay,
    }

    save_steps(steps)
    return jsonify({"status": "success", "steps": steps})


@app.route("/api/steps/delete", methods=["POST"])
def delete_step():
    data = request.json
    step_name = data["name"]
    steps = load_steps()

    if step_name in steps:
        del steps[step_name]
        save_steps(steps)
        return jsonify({"status": "success", "steps": steps})
    return jsonify({"status": "error", "message": "Paso no encontrado"}), 400


# Ejecutar un solo paso desde el botón de la tabla
@app.route("/api/steps/run_single", methods=["POST"])
def run_single_step():
    data = request.json
    step_name = data["name"]
    steps = load_steps()

    if step_name in steps:
        tiempo_viaje = ejecutar_paso_individual(steps[step_name])
        return jsonify(
            {"status": "success", "estimated_time_seconds": round(tiempo_viaje, 3)}
        )
    return jsonify({"status": "error", "message": "Paso no encontrado"}), 400


# EJECUTAR SECUENCIA PASO A PASO (MOTOR POR MOTOR)
@app.route("/api/sequence/run", methods=["POST"])
def run_sequence():
    data = request.json
    sequence_names = data.get("sequence", [])
    steps = load_steps()

    for name in sequence_names:
        # Soporte para la acción especial "HOME" intercalada
        if name == "[ IR A HOME COMIENZO ]":
            comando_bulk = ""
            tiempos_home = []
            for ch, config in SERVOS_CONFIG.items():
                enviar_comando(f"V {ch} 4")
                tiempos_home.append(
                    calcular_movimiento_suave(ch, config["home"], 4)
                )
                config["current"] = config["home"]
                comando_bulk += f"{ch} {config['home']} "
            enviar_comando(comando_bulk.strip())

            # Esperar a que todos los motores lleguen a Home antes de continuar
            time.sleep(max(tiempos_home) if tiempos_home else 1)
            continue

        if name in steps:
            step_data = steps[name]
            retardo_extra = float(step_data.get("delay", 0.5))

            # 1. Mover el motor específico y obtener su tiempo de recorrido exacto
            tiempo_viaje = ejecutar_paso_individual(step_data)

            # 2. Bloquear la ejecución hasta que ese motor termine, más su retraso
            tiempo_total = tiempo_viaje + retardo_extra
            print(
                f"Secuencia -> Motor {step_data['channel']} moviéndose. Esperando {round(tiempo_total,2)}s"
            )
            time.sleep(tiempo_total)

    return jsonify({"status": "success"})


HTML_INTERFACE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Control Secuencial Motor a Motor</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 20px; color: #333; }
        .container { max-width: 1100px; margin: 0 auto; }
        h1, h2 { text-align: center; color: #2c3e50; margin-top: 5px;}
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media(max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        .card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .servo-control { border-bottom: 1px solid #eee; padding: 12px 0; }
        .servo-header { display: flex; justify-content: space-between; font-weight: bold; color: #34495e; }
        .controls { display: flex; align-items: center; gap: 10px; margin-top: 5px; }
        button { color: white; border: none; padding: 8px 12px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        .btn-blue { background: #3498db; } .btn-blue:hover { background: #2980b9; }
        .btn-orange { background: #e67e22; } .btn-orange:hover { background: #d35400; }
        .btn-green { background: #2ecc71; } .btn-green:hover { background: #27ae60; }
        .btn-red { background: #e74c3c; } .btn-red:hover { background: #c0392b; }
        input[type="range"] { flex-grow: 1; }
        
        .pose-table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        .pose-table th, .pose-table td { border: 1px solid #ddd; padding: 10px; text-align: center; }
        .pose-table th { background-color: #f4f6f7; }
        .pose-table input { text-align: center; padding: 4px; border-radius: 4px; border:1px solid #ccc; }

        .info-box { background: #e8f4fd; border-left: 4px solid #3498db; padding: 12px; margin-bottom: 15px; border-radius: 0 6px 6px 0; text-align: center; font-weight: bold;}
        .seq-builder { background: #fdfefe; border: 2px dashed #bdc3c7; padding: 15px; border-radius: 8px; text-align: center; margin-top: 10px;}
        .creator-row { display: flex; gap: 10px; background: #f9f9f9; padding: 15px; border-radius: 8px; border: 1px solid #e0e0e0; margin-bottom: 15px; flex-wrap: wrap; align-items: center;}
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Secuenciador de Motores Individuales</h1>
        <div id="info-global" class="info-box">Última acción: Esperando comandos...</div>

        <div style="text-align: center; margin-bottom: 20px;">
            <button class="btn-blue" style="background: #9b59b6; font-size: 1.1em; padding: 10px 25px;" onclick="irAHome Global()">🏠 ENVIAR TODO A HOME</button>
        </div>

        <div class="grid">
            <div class="card">
                <h2>👈 Brazo 1 (Izquierdo)</h2>
                <div id="brazo1-container"></div>
            </div>
            <div class="card">
                <h2>👉 Brazo 2 (Derecho)</h2>
                <div id="brazo2-container"></div>
            </div>
        </div>

        <div class="card">
            <h2>💾 Biblioteca de Pasos (Motor Único)</h2>
            
            <div class="creator-row">
                <input type="text" id="step-name" placeholder="Nombre del paso (Ej: 'Mover Hombro')" style="flex-grow: 2; padding: 8px;">
                
                <select id="step-channel" style="padding: 8px;" onchange="actualizarAnguloSugerido()">
                    <option value="0">Ch 0 - Brazo 1 Hombro</option>
                    <option value="1">Ch 1 - Brazo 1 Codo Rot</option>
                    <option value="2">Ch 2 - Brazo 1 Codo Vert</option>
                    <option value="4">Ch 4 - Brazo 2 Hombro</option>
                    <option value="5">Ch 5 - Brazo 2 Codo Rot</option>
                    <option value="6">Ch 6 - Brazo 2 Codo Vert</option>
                </select>

                <div style="display: flex; align-items: center; gap: 5px;">
                    <label>Ángulo:</label>
                    <input type="number" id="step-angle" style="width: 60px; padding: 6px;">°
                </div>

                <div style="display: flex; align-items: center; gap: 5px;">
                    <label>Velocidad:</label>
                    <input type="number" id="step-interval" value="5" style="width: 50px; padding: 6px;"> ms
                </div>

                <div style="display: flex; align-items: center; gap: 5px;">
                    <label>Retardo:</label>
                    <input type="number" id="step-delay" value="0.5" step="0.1" style="width: 50px; padding: 6px;"> s
                </div>

                <button class="btn-green" onclick="crearPaso()">Crear / Modificar Paso</button>
            </div>

            <table class="pose-table">
                <thead>
                    <tr>
                        <th>Identificador del Paso</th>
                        <th>Motor / Canal</th>
                        <th>Ángulo Destino</th>
                        <th>Velocidad (ms/paso)</th>
                        <th>Retardo (s)</th>
                        <th>Acciones</th>
                    </tr>
                </thead>
                <tbody id="steps-table-body"></tbody>
            </table>
        </div>

        <div class="card">
            <h2>⛓️ Constructor de Rutas e Historial Secuencial</h2>
            <p style="font-size: 0.9em; color: #666;">Diseña la coreografía agregando los pasos guardados en el orden exacto en que quieres que se ejecuten.</p>
            <div class="seq-builder">
                <div style="margin-bottom: 10px;">
                    <button class="btn-blue" style="background:#34495e;" onclick="agregarACola('[ IR A HOME COMIENZO ]')">+ Añadir Reset a Home</button>
                </div>
                <div id="sequence-queue" style="font-weight: bold; margin-bottom: 15px; font-size: 1.1em; color: #2c3e50; border: 1px solid #ddd; padding: 10px; background: #fcfcfc;">[ Secuencia Vacía ]</div>
                <button class="btn-green" style="font-size: 1.2em; padding: 12px 25px;" onclick="ejecutarSecuencia()">▶️ LANZAR SECUENCIA DE PASOS</button>
                <button class="btn-red" onclick="limpiarSecuencia()">Limpiar Orden</button>
            </div>
        </div>
    </div>

    <script>
        let listaSecuencia = [];
        let servosCached = {};

        async function actualizarEstado() {
            const res = await fetch('/api/status');
            const data = await res.json();
            servosCached = data.servos;
            
            renderBrazo([0, 1, 2], 'brazo1-container', data.servos);
            renderBrazo([4, 5, 6], 'brazo2-container', data.servos);
            renderTablaPasos(data.steps);
        }

        function renderBrazo(canales, containerId, servos) {
            const container = document.getElementById(containerId);
            container.innerHTML = '';
            canales.forEach(ch => {
                const s = servos[ch];
                container.innerHTML += `
                    <div class="servo-control">
                        <div class="servo-header"><span>${s.name} (Ch ${ch})</span><span style="color:#3498db" id="val-display-${ch}">${s.current}°</span></div>
                        <div class="controls">
                            <button class="btn-orange" onclick="mover(${ch}, 'step_down')">-1°</button>
                            <input type="range" min="${s.min}" max="${s.max}" value="${s.current}" id="slider-${ch}" onchange="mover(${ch}, 'angle', this.value)" oninput="document.getElementById('val-display-${ch}').innerText = this.value + '°'">
                            <button class="btn-orange" onclick="mover(${ch}, 'step_up')">+1°</button>
                        </div>
                    </div>`;
            });
        }

        function actualizarAnguloSugerido() {
            const ch = document.getElementById('step-channel').value;
            if(servosCached[ch]) {
                document.getElementById('step-angle').value = servosCached[ch].current;
            }
        }

        function renderTablaPasos(steps) {
            const tbody = document.getElementById('steps-table-body');
            tbody.innerHTML = '';
            
            Object.keys(steps).forEach(name => {
                const s = steps[name];
                const motorName = servosCached[s.channel] ? servosCached[s.channel].name : `Canal ${s.channel}`;
                
                tbody.innerHTML += `
                    <tr>
                        <td style="font-weight:bold; color: #2c3e50;">${name}</td>
                        <td>${motorName} (Ch ${s.channel})</td>
                        <td><b style="color:#27ae60">${s.angle}°</b></td>
                        <td><input type="number" style="width:50px" value="${s.interval}" onchange="modificarFila('${name}', 'interval', this.value)"> ms</td>
                        <td><input type="number" style="width:50px" step="0.1" value="${s.delay}" onchange="modificarFila('${name}', 'delay', this.value)"> s</td>
                        <td>
                            <button class="btn-blue" onclick="ejecutarPasoUnico('${name}')">Probar</button>
                            <button class="btn-orange" style="background:#9b59b6;" onclick="agregarACola('${name}')">+ Secuencia</button>
                            <button class="btn-red" onclick="eliminarPaso('${name}')">🗑️</button>
                        </td>
                    </tr>`;
            });
        }

        async function mover(channel, action, value = 0) {
            const res = await fetch('/api/move', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({channel, action, value})
            });
            const data = await res.json();
            if(data.status === 'success') {
                document.getElementById(`slider-${channel}`).value = data.current_angle;
                document.getElementById(`val-display-${channel}`).innerText = data.current_angle + "°";
                document.getElementById('info-global').innerHTML = `Movimiento Manual: Canal ${channel} -> ${data.current_angle}°.`;
                actualizarAnguloSugerido();
            }
        }

        async function irAHomeGlobal() {
            await fetch('/api/home', {method: 'POST'});
            document.getElementById('info-global').innerText = "Todos los brazos reposicionados en Home.";
            actualizarEstado();
        }

        async function crearPaso() {
            const name = document.getElementById('step-name').value.trim();
            const channel = document.getElementById('step-channel').value;
            const angle = document.getElementById('step-angle').value;
            const interval = document.getElementById('step-interval').value;
            const delay = document.getElementById('step-delay').value;

            if(!name) return alert("Por favor escribe un nombre para identificar el paso.");
            if(angle === "") return alert("Especifica un ángulo.");

            await fetch('/api/steps', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, channel, angle: parseInt(angle), interval: parseInt(interval), delay: parseFloat(delay)})
            });
            document.getElementById('step-name').value = '';
            actualizarEstado();
        }

        async function modificarFila(name, campo, valor) {
            const res = await fetch('/api/status');
            const data = await res.json();
            const paso = data.steps[name];

            paso[campo] = campo === 'interval' ? parseInt(valor) : parseFloat(valor);

            await fetch('/api/steps', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, channel: paso.channel, angle: paso.angle, interval: paso.interval, delay: paso.delay})
            });
        }

        async function eliminarPaso(name) {
            await fetch('/api/steps/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name})
            });
            actualizarEstado();
        }

        async function ejecutarPasoUnico(name) {
            const res = await fetch('/api/steps/run_single', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name})
            });
            const data = await res.json();
            document.getElementById('info-global').innerHTML = `Probando paso [${name}]. Tiempo de tránsito: ~<b>${data.estimated_time_seconds}s</b>`;
            actualizarEstado();
        }

        function agregarACola(name) {
            listaSecuencia.push(name);
            actualizarVisualizacionCola();
        }

        function limpiarSecuencia() {
            listaSecuencia = [];
            actualizarVisualizacionCola();
        }

        function actualizarVisualizacionCola() {
            const q = document.getElementById('sequence-queue');
            q.innerText = listaSecuencia.length === 0 ? "[ Secuencia Vacía ]" : listaSecuencia.join(" ➔ ");
        }

        async function ejecutarSecuencia() {
            if(listaSecuencia.length === 0) return alert("Añade pasos a la secuencia primero.");
            document.getElementById('info-global').innerHTML = "⏳ <b>Ejecutando coreografía paso a paso en el robot...</b>";
            
            await fetch('/api/sequence/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ sequence: listaSecuencia })
            });
            
            document.getElementById('info-global').innerHTML = "✅ <b>Secuencia ordenada finalizada correctamente.</b>";
            actualizarEstado();
        }

        actualizarEstado();
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)