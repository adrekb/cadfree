/** Minimal STL + orbit viewer so Cadfree does not depend on Three's Controls class. */

const canvas = document.getElementById('cad-canvas');
const gl = canvas.getContext('webgl');
if (!gl) {
    window.cadfreeViewer = { load() {} };
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
      void main() {
        vec3 n = normalize(vN);
        float l = 0.35 + 0.65 * max(dot(n, normalize(vec3(0.4, 0.9, 0.3))), 0.0);
        gl_FragColor = vec4(0.957, 0.506, 0.247 * 0 + 0.247, 1.0) * vec4(l, l, l, 1.0);
        gl_FragColor.rgb = vec3(0.96, 0.51, 0.25) * l + vec3(0.08, 0.09, 0.12);
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
    const vbo = gl.createBuffer();
    let vertCount = 0;
    let radius = 80;
    let yaw = 0.7;
    let pitch = 0.5;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;

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
    function perspective(fovy, aspect, near, far) {
        const f = 1 / Math.tan(fovy / 2);
        const m = new Float32Array(16);
        m[0] = f / aspect; m[5] = f; m[10] = (far + near) / (near - far); m[11] = -1;
        m[14] = (2 * far * near) / (near - far);
        return m;
    }
    function lookAt(eye) {
        const z = normalize(eye);
        const x = normalize(cross([0, 1, 0], z));
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

    function load(url) {
        fetch(url).then((r) => { if (!r.ok) throw new Error(); return r.arrayBuffer(); })
            .then((buf) => {
                const mesh = parseStl(buf);
                vertCount = mesh.positions.length / 3;
                const interleaved = new Float32Array(vertCount * 6);
                let cx = 0, cy = 0, cz = 0;
                for (let i = 0; i < vertCount; i++) {
                    cx += mesh.positions[i * 3]; cy += mesh.positions[i * 3 + 1]; cz += mesh.positions[i * 3 + 2];
                }
                cx /= vertCount; cy /= vertCount; cz /= vertCount;
                let maxR = 1;
                for (let i = 0; i < vertCount; i++) {
                    const x = mesh.positions[i * 3] - cx;
                    const y = mesh.positions[i * 3 + 1] - cy;
                    const z = mesh.positions[i * 3 + 2] - cz;
                    interleaved[i * 6] = x; interleaved[i * 6 + 1] = y; interleaved[i * 6 + 2] = z;
                    interleaved[i * 6 + 3] = mesh.normals[i * 3];
                    interleaved[i * 6 + 4] = mesh.normals[i * 3 + 1];
                    interleaved[i * 6 + 5] = mesh.normals[i * 3 + 2];
                    maxR = Math.max(maxR, Math.hypot(x, y, z));
                }
                radius = maxR * 2.6;
                gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
                gl.bufferData(gl.ARRAY_BUFFER, interleaved, gl.STATIC_DRAW);
            }).catch(() => { vertCount = 0; });
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
        if (vertCount) {
            const eye = [
                radius * Math.cos(pitch) * Math.sin(yaw),
                radius * Math.sin(pitch),
                radius * Math.cos(pitch) * Math.cos(yaw),
            ];
            const mvp = matMul(perspective(0.7, w / h, 0.5, radius * 20), lookAt(eye));
            gl.useProgram(prog);
            gl.uniformMatrix4fv(uMVP, false, mvp);
            gl.uniformMatrix4fv(uN, false, lookAt(eye));
            gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
            gl.enableVertexAttribArray(aPos);
            gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 24, 0);
            gl.enableVertexAttribArray(aNrm);
            gl.vertexAttribPointer(aNrm, 3, gl.FLOAT, false, 24, 12);
            gl.drawArrays(gl.TRIANGLES, 0, vertCount);
        }
        requestAnimationFrame(tick);
    }
    tick();
    window.cadfreeViewer = { load };
}
