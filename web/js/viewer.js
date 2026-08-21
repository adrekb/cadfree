/** Instanced STL viewer: unique meshes drawn N times. No Three.js. */

const canvas = document.getElementById('cad-canvas');
const gl = canvas.getContext('webgl');
if (!gl) {
    window.cadfreeViewer = { load() {}, loadScene() {} };
} else {
    const vs = `
      attribute vec3 aPos; attribute vec3 aNrm;
      uniform mat4 uMVP; uniform mat4 uN;
      varying vec3 vN;
      void main() {
        vN = mat3(uN) * aNrm;
        gl_Position = uMVP * vec4(aPos, 1.0);
      }`;
    const fs = `
      precision mediump float;
      varying vec3 vN;
      uniform vec3 uColor;
      void main() {
        vec3 n = normalize(vN);
        float l = 0.35 + 0.65 * max(dot(n, normalize(vec3(0.4, 0.9, 0.3))), 0.0);
        gl_FragColor = vec4(uColor * l + vec3(0.08, 0.09, 0.12), 1.0);
      }`;

    function compile(type, src) {
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        return s;
    }
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(prog);
    gl.useProgram(prog);
    const aPos = gl.getAttribLocation(prog, 'aPos');
    const aNrm = gl.getAttribLocation(prog, 'aNrm');
    const uMVP = gl.getUniformLocation(prog, 'uMVP');
    const uN = gl.getUniformLocation(prog, 'uN');
    const uColor = gl.getUniformLocation(prog, 'uColor');

    const IDENTITY = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    let meshes = {};
    let draws = [];
    let radius = 80;
    let yaw = 0.7;
    let pitch = 0.5;
    let target = [0, 0, 0];
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let loadGen = 0;

    canvas.addEventListener('pointerdown', (e) => {
        dragging = true; lastX = e.clientX; lastY = e.clientY; canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointerup', () => { dragging = false; });
    canvas.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        yaw += (e.clientX - lastX) * 0.01;
        pitch = Math.max(-1.4, Math.min(1.4, pitch + (e.clientY - lastY) * 0.01));
        lastX = e.clientX; lastY = e.clientY;
    });
    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        radius *= e.deltaY > 0 ? 1.08 : 0.92;
    }, { passive: false });

    function parseStl(buffer) {
        const view = new DataView(buffer);
        const faces = view.getUint32(80, true);
        const positions = [];
        const normals = [];
        let offset = 84;
        for (let i = 0; i < faces; i++) {
            const nx = view.getFloat32(offset, true);
            const ny = view.getFloat32(offset + 4, true);
            const nz = view.getFloat32(offset + 8, true);
            offset += 12;
            for (let v = 0; v < 3; v++) {
                positions.push(view.getFloat32(offset, true), view.getFloat32(offset + 4, true), view.getFloat32(offset + 8, true));
                normals.push(nx, ny, nz);
                offset += 12;
            }
            offset += 2;
        }
        return { positions: new Float32Array(positions), normals: new Float32Array(normals) };
    }

    function matMul(a, b) {
        const o = new Float32Array(16);
        for (let c = 0; c < 4; c++) {
            for (let r = 0; r < 4; r++) {
                o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
            }
        }
        return o;
    }
    function transformPoint(m, p) {
        return [
            m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
            m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
            m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
        ];
    }
    function perspective(fovy, aspect, near, far) {
        const f = 1 / Math.tan(fovy / 2);
        const m = new Float32Array(16);
        m[0] = f / aspect; m[5] = f; m[10] = (far + near) / (near - far); m[11] = -1;
        m[14] = (2 * far * near) / (near - far);
        return m;
    }
    function lookAt(eye, center) {
        const z = normalize([eye[0] - center[0], eye[1] - center[1], eye[2] - center[2]]);
        let x = normalize(cross([0, 1, 0], z));
        if (!isFinite(x[0]) || Math.hypot(x[0], x[1], x[2]) < 1e-6) {
            x = normalize(cross([1, 0, 0], z));
        }
        const y = cross(z, x);
        const m = new Float32Array(16);
        m[0] = x[0]; m[1] = y[0]; m[2] = z[0];
        m[4] = x[1]; m[5] = y[1]; m[6] = z[1];
        m[8] = x[2]; m[9] = y[2]; m[10] = z[2];
        m[12] = -dot(x, eye); m[13] = -dot(y, eye); m[14] = -dot(z, eye); m[15] = 1;
        return m;
    }
    function normalize(v) {
        const l = Math.hypot(v[0], v[1], v[2]) || 1;
        return [v[0] / l, v[1] / l, v[2] / l];
    }
    function cross(a, b) {
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    }
    function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

    function meshFromStl(buffer, color) {
        const parsed = parseStl(buffer);
        const vertCount = parsed.positions.length / 3;
        const interleaved = new Float32Array(vertCount * 6);
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        for (let i = 0; i < vertCount; i++) {
            const x = parsed.positions[i * 3];
            const y = parsed.positions[i * 3 + 1];
            const z = parsed.positions[i * 3 + 2];
            interleaved[i * 6] = x; interleaved[i * 6 + 1] = y; interleaved[i * 6 + 2] = z;
            interleaved[i * 6 + 3] = parsed.normals[i * 3];
            interleaved[i * 6 + 4] = parsed.normals[i * 3 + 1];
            interleaved[i * 6 + 5] = parsed.normals[i * 3 + 2];
            minX = Math.min(minX, x); minY = Math.min(minY, y); minZ = Math.min(minZ, z);
            maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); maxZ = Math.max(maxZ, z);
        }
        const vbo = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
        gl.bufferData(gl.ARRAY_BUFFER, interleaved, gl.STATIC_DRAW);
        return {
            vbo,
            count: vertCount,
            color: color || [0.96, 0.51, 0.25],
            bbox: [[minX, minY, minZ], [maxX, maxY, maxZ]],
        };
    }

    function fitCamera() {
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        let any = false;
        for (const d of draws) {
            const mesh = meshes[d.part_id];
            if (!mesh || !mesh.bbox) continue;
            const m = d.matrix;
            const a = mesh.bbox[0];
            const b = mesh.bbox[1];
            for (const x of [a[0], b[0]]) {
                for (const y of [a[1], b[1]]) {
                    for (const z of [a[2], b[2]]) {
                        const p = transformPoint(m, [x, y, z]);
                        minX = Math.min(minX, p[0]); minY = Math.min(minY, p[1]); minZ = Math.min(minZ, p[2]);
                        maxX = Math.max(maxX, p[0]); maxY = Math.max(maxY, p[1]); maxZ = Math.max(maxZ, p[2]);
                        any = true;
                    }
                }
            }
        }
        if (!any) {
            target = [0, 0, 0];
            radius = 80;
            return;
        }
        target = [(minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2];
        radius = Math.max(8, Math.hypot(maxX - minX, maxY - minY, maxZ - minZ) * 1.3);
    }

    function load(url) {
        loadScene({
            scene_parts: [{ id: 'one', stl_url: url, color: [0.96, 0.51, 0.25] }],
            draws: [{ part_id: 'one', matrix: Array.from(IDENTITY) }],
        });
    }

    function loadScene(scene) {
        const gen = ++loadGen;
        const parts = scene.scene_parts || scene.parts || [];
        draws = (scene.draws || []).map((d) => ({
            part_id: d.part_id,
            instance_id: d.instance_id,
            matrix: new Float32Array(d.matrix && d.matrix.length === 16 ? d.matrix : IDENTITY),
        }));
        const needed = {};
        parts.forEach((p) => { if (p.stl_url) needed[p.id] = p; });
        draws.forEach((d) => { if (!needed[d.part_id]) { /* skip */ } });
        const jobs = Object.values(needed).map((p) =>
            fetch(p.stl_url).then((r) => { if (!r.ok) throw new Error(); return r.arrayBuffer(); })
                .then((buf) => ({ id: p.id, mesh: meshFromStl(buf, p.color) }))
                .catch(() => null)
        );
        Promise.all(jobs).then((loaded) => {
            if (gen !== loadGen) return;
            meshes = {};
            loaded.forEach((item) => { if (item) meshes[item.id] = item.mesh; });
            fitCamera();
        });
    }

    function tick() {
        const w = canvas.clientWidth || 400;
        const h = canvas.clientHeight || 280;
        if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w; canvas.height = h;
            gl.viewport(0, 0, w, h);
        }
        gl.clearColor(0.07, 0.08, 0.1, 1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        gl.enable(gl.DEPTH_TEST);
        const eye = [
            target[0] + radius * Math.cos(pitch) * Math.sin(yaw),
            target[1] + radius * Math.sin(pitch),
            target[2] + radius * Math.cos(pitch) * Math.cos(yaw),
        ];
        const view = lookAt(eye, target);
        const projView = matMul(perspective(0.7, w / h, Math.max(0.5, radius * 0.02), radius * 40), view);
        gl.useProgram(prog);
        for (const d of draws) {
            const mesh = meshes[d.part_id];
            if (!mesh || !mesh.count) continue;
            const mvp = matMul(projView, d.matrix);
            const nmat = matMul(view, d.matrix);
            gl.uniformMatrix4fv(uMVP, false, mvp);
            gl.uniformMatrix4fv(uN, false, nmat);
            gl.uniform3fv(uColor, mesh.color);
            gl.bindBuffer(gl.ARRAY_BUFFER, mesh.vbo);
            gl.enableVertexAttribArray(aPos);
            gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 24, 0);
            gl.enableVertexAttribArray(aNrm);
            gl.vertexAttribPointer(aNrm, 3, gl.FLOAT, false, 24, 12);
            gl.drawArrays(gl.TRIANGLES, 0, mesh.count);
        }
        requestAnimationFrame(tick);
    }
    tick();
    window.cadfreeViewer = { load, loadScene, applyDraws: function (next) {
        if (!next || !next.length) return;
        draws = next.map((d) => ({
            part_id: d.part_id,
            instance_id: d.instance_id,
            matrix: new Float32Array(d.matrix && d.matrix.length === 16 ? d.matrix : IDENTITY),
        }));
    } };
}
