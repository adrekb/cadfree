/** Instanced STL viewer: unique meshes drawn N times. Click a fillet to pick it. */

const canvas = document.getElementById('cad-canvas');
const gl = canvas.getContext('webgl');
if (!gl) {
    window.cadfreeViewer = { load() {}, loadScene() {}, highlight() {}, applyDraws() {} };
} else {
    const vs = `
      attribute vec3 aPos; attribute vec3 aNrm; attribute float aFeat;
      uniform mat4 uMVP; uniform mat4 uN;
      varying vec3 vN; varying float vFeat;
      void main() {
        vN = mat3(uN) * aNrm;
        vFeat = aFeat;
        gl_Position = uMVP * vec4(aPos, 1.0);
      }`;
    const fs = `
      precision mediump float;
      varying vec3 vN; varying float vFeat;
      uniform vec3 uColor; uniform vec3 uHi; uniform float uPick;
      void main() {
        vec3 n = normalize(vN);
        float l = 0.35 + 0.65 * max(dot(n, normalize(vec3(0.4, 0.9, 0.3))), 0.0);
        vec3 base = uColor;
        if (uPick > 0.5 && abs(vFeat - uPick) < 0.5) base = uHi;
        gl_FragColor = vec4(base * l + vec3(0.08, 0.09, 0.12), 1.0);
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
    const aFeat = gl.getAttribLocation(prog, 'aFeat');
    const uMVP = gl.getUniformLocation(prog, 'uMVP');
    const uN = gl.getUniformLocation(prog, 'uN');
    const uColor = gl.getUniformLocation(prog, 'uColor');
    const uHi = gl.getUniformLocation(prog, 'uHi');
    const uPick = gl.getUniformLocation(prog, 'uPick');

    const IDENTITY = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    let meshes = {};
    let draws = [];
    let radius = 80;
    let yaw = 0.7;
    let pitch = 0.5;
    let target = [0, 0, 0];
    let dragging = false;
    let dragMoved = 0;
    let lastX = 0;
    let lastY = 0;
    let loadGen = 0;
    let selectedPick = 0;
    let lastEye = [0, 0, 80];
    let lastProjView = IDENTITY;

    canvas.addEventListener('pointerdown', (e) => {
        dragging = true; dragMoved = 0; lastX = e.clientX; lastY = e.clientY;
        canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointerup', (e) => {
        const click = dragging && dragMoved < 5;
        dragging = false;
        if (click) pickAt(e);
    });
    canvas.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        const dx = e.clientX - lastX;
        const dy = e.clientY - lastY;
        dragMoved += Math.hypot(dx, dy);
        yaw += dx * 0.01;
        pitch = Math.max(-1.4, Math.min(1.4, pitch + dy * 0.01));
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
        const featIds = [];
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
            featIds.push(view.getUint16(offset, true));
            offset += 2;
        }
        return {
            positions: new Float32Array(positions),
            normals: new Float32Array(normals),
            featIds: new Uint16Array(featIds),
        };
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
    function invert4(m) {
        const o = new Float32Array(16);
        const a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3];
        const a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7];
        const a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11];
        const a30 = m[12], a31 = m[13], a32 = m[14], a33 = m[15];
        const b00 = a00 * a11 - a01 * a10;
        const b01 = a00 * a12 - a02 * a10;
        const b02 = a00 * a13 - a03 * a10;
        const b03 = a01 * a12 - a02 * a11;
        const b04 = a01 * a13 - a03 * a11;
        const b05 = a02 * a13 - a03 * a12;
        const b06 = a20 * a31 - a21 * a30;
        const b07 = a20 * a32 - a22 * a30;
        const b08 = a20 * a33 - a23 * a30;
        const b09 = a21 * a32 - a22 * a31;
        const b10 = a21 * a33 - a23 * a31;
        const b11 = a22 * a33 - a23 * a32;
        let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
        if (Math.abs(det) < 1e-12) return null;
        det = 1 / det;
        o[0] = (a11 * b11 - a12 * b10 + a13 * b09) * det;
        o[1] = (a02 * b10 - a01 * b11 - a03 * b09) * det;
        o[2] = (a31 * b05 - a32 * b04 + a33 * b03) * det;
        o[3] = (a22 * b04 - a21 * b05 - a23 * b03) * det;
        o[4] = (a12 * b08 - a10 * b11 - a13 * b07) * det;
        o[5] = (a00 * b11 - a02 * b08 + a03 * b07) * det;
        o[6] = (a32 * b02 - a30 * b05 - a33 * b01) * det;
        o[7] = (a20 * b05 - a22 * b02 + a23 * b01) * det;
        o[8] = (a10 * b10 - a11 * b08 + a13 * b06) * det;
        o[9] = (a01 * b08 - a00 * b10 - a03 * b06) * det;
        o[10] = (a30 * b04 - a31 * b02 + a33 * b00) * det;
        o[11] = (a21 * b02 - a20 * b04 - a23 * b00) * det;
        o[12] = (a11 * b07 - a10 * b09 - a12 * b06) * det;
        o[13] = (a00 * b09 - a01 * b07 + a02 * b06) * det;
        o[14] = (a31 * b01 - a30 * b03 - a32 * b00) * det;
        o[15] = (a20 * b03 - a21 * b01 + a22 * b00) * det;
        return o;
    }
    function transformH(m, p) {
        const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12] * p[3];
        const y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13] * p[3];
        const z = m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14] * p[3];
        const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15] * p[3] || 1;
        return [x / w, y / w, z / w];
    }
    function normalize(v) {
        const l = Math.hypot(v[0], v[1], v[2]) || 1;
        return [v[0] / l, v[1] / l, v[2] / l];
    }
    function cross(a, b) {
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[2]];
    }
    function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

    function rayTri(orig, dir, v0, v1, v2) {
        const eps = 1e-7;
        const e1 = [v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]];
        const e2 = [v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]];
        const pvec = cross(dir, e2);
        const det = dot(e1, pvec);
        if (Math.abs(det) < eps) return null;
        const inv = 1 / det;
        const tvec = [orig[0] - v0[0], orig[1] - v0[1], orig[2] - v0[2]];
        const u = dot(tvec, pvec) * inv;
        if (u < 0 || u > 1) return null;
        const qvec = cross(tvec, e1);
        const v = dot(dir, qvec) * inv;
        if (v < 0 || u + v > 1) return null;
        const t = dot(e2, qvec) * inv;
        return t > eps ? t : null;
    }

    function meshFromStl(buffer, color) {
        const parsed = parseStl(buffer);
        const vertCount = parsed.positions.length / 3;
        const interleaved = new Float32Array(vertCount * 7);
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        for (let i = 0; i < vertCount; i++) {
            const x = parsed.positions[i * 3];
            const y = parsed.positions[i * 3 + 1];
            const z = parsed.positions[i * 3 + 2];
            const feat = parsed.featIds[Math.floor(i / 3)] || 0;
            interleaved[i * 7] = x; interleaved[i * 7 + 1] = y; interleaved[i * 7 + 2] = z;
            interleaved[i * 7 + 3] = parsed.normals[i * 3];
            interleaved[i * 7 + 4] = parsed.normals[i * 3 + 1];
            interleaved[i * 7 + 5] = parsed.normals[i * 3 + 2];
            interleaved[i * 7 + 6] = feat;
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
            positions: parsed.positions,
            featIds: parsed.featIds,
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

    function pickAt(ev) {
        const rect = canvas.getBoundingClientRect();
        const x = ev.clientX - rect.left;
        const y = ev.clientY - rect.top;
        const w = canvas.width || 1;
        const h = canvas.height || 1;
        const inv = invert4(lastProjView);
        if (!inv) return;
        const ndcX = (x / (rect.width || w)) * 2 - 1;
        const ndcY = 1 - (y / (rect.height || h)) * 2;
        const far = transformH(inv, [ndcX, ndcY, 1, 1]);
        const dir = normalize([far[0] - lastEye[0], far[1] - lastEye[1], far[2] - lastEye[2]]);
        let bestT = Infinity;
        let bestFeat = 0;
        for (const d of draws) {
            const mesh = meshes[d.part_id];
            if (!mesh || !mesh.positions || !mesh.featIds) continue;
            const m = d.matrix;
            const tris = mesh.featIds.length;
            for (let t = 0; t < tris; t++) {
                const i = t * 9;
                const a = transformPoint(m, [mesh.positions[i], mesh.positions[i + 1], mesh.positions[i + 2]]);
                const b = transformPoint(m, [mesh.positions[i + 3], mesh.positions[i + 4], mesh.positions[i + 5]]);
                const c = transformPoint(m, [mesh.positions[i + 6], mesh.positions[i + 7], mesh.positions[i + 8]]);
                const hit = rayTri(lastEye, dir, a, b, c);
                if (hit != null && hit < bestT) {
                    bestT = hit;
                    bestFeat = mesh.featIds[t] || 0;
                }
            }
        }
        selectedPick = bestFeat;
        if (typeof window.cadfreeViewer.onPick === 'function') {
            window.cadfreeViewer.onPick(bestFeat);
        }
    }

    function highlight(pick) {
        selectedPick = Number(pick) || 0;
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
        lastEye = eye;
        lastProjView = projView;
        gl.useProgram(prog);
        gl.uniform3fv(uHi, [1.0, 0.72, 0.28]);
        gl.uniform1f(uPick, selectedPick);
        const stride = 28;
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
            gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, stride, 0);
            gl.enableVertexAttribArray(aNrm);
            gl.vertexAttribPointer(aNrm, 3, gl.FLOAT, false, stride, 12);
            if (aFeat >= 0) {
                gl.enableVertexAttribArray(aFeat);
                gl.vertexAttribPointer(aFeat, 1, gl.FLOAT, false, stride, 24);
            }
            gl.drawArrays(gl.TRIANGLES, 0, mesh.count);
        }
        requestAnimationFrame(tick);
    }
    tick();
    window.cadfreeViewer = {
        load, loadScene, highlight, onPick: null,
        applyDraws: function (next) {
            if (!next || !next.length) return;
            draws = next.map((d) => ({
                part_id: d.part_id,
                instance_id: d.instance_id,
                matrix: new Float32Array(d.matrix && d.matrix.length === 16 ? d.matrix : IDENTITY),
            }));
        },
    };
}
