import http from 'node:http';
import { pathToFileURL } from 'node:url';

export function createRuntimeProxy({
  socketPath = '/var/run/docker.sock',
  workspace = process.env.WORKSPACE_CONTAINER,
  browser = process.env.BROWSER_CONTAINER,
} = {}) {
  if (!/^nod-workspace-[a-zA-Z0-9_-]+$/.test(workspace || ''))
    throw new Error('A workspace container is required.');
  if (!/^nod-browser-[a-zA-Z0-9_-]+$/.test(browser || ''))
    throw new Error('A browser container is required.');
  const executions = new Map();
  const denied = (response) => response.writeHead(403).end('Runtime operation refused.');
  const gateway = http.createServer(async (request, response) => {
    const path = request.url?.replace(/^\/v1\.\d+(?=\/)/, '') || '';
    const method = request.method;
    const now = Date.now();
    for (const [id, created] of executions) if (now - created > 3_600_000) executions.delete(id);
    let body;
    let creating = false;
    let inspecting;
    try {
      if ((method === 'GET' || method === 'HEAD') && path === '/_ping') {
        response.writeHead(200, { 'API-Version': '1.44', 'Docker-Experimental': 'false' });
        return response.end('OK');
      }
      if (method === 'GET' && path === '/version') {
        response.writeHead(200, { 'Content-Type': 'application/json' });
        return response.end(JSON.stringify({ ApiVersion: '1.44', MinAPIVersion: '1.44' }));
      }
      const create = path.match(/^\/containers\/([^/]+)\/exec$/);
      const inspect = path.match(/^\/containers\/([^/]+)\/json$/);
      const execute = path.match(/^\/exec\/([a-f0-9]{64})\/(start|json)$/);
      if (method === 'GET' && inspect && [workspace, browser].includes(inspect[1])) {
        inspecting = inspect[1];
      } else if (method === 'POST' && create && [workspace, browser].includes(create[1])) {
        if (executions.size >= 4096) return response.writeHead(503).end();
        body = await readJson(request);
        const allowed = new Set([
          'User',
          'WorkingDir',
          'Cmd',
          'AttachStdin',
          'AttachStdout',
          'AttachStderr',
          'Tty',
          'Privileged',
          'Env',
          'DetachKeys',
          'Detach',
        ]);
        if (
          Object.keys(body).some((key) => !allowed.has(key)) ||
          (body.Privileged !== undefined && body.Privileged !== false) ||
          (body.Detach !== undefined && body.Detach !== false) ||
          (body.Tty !== undefined && body.Tty !== false) ||
          (body.Env !== undefined &&
            body.Env !== null &&
            !(Array.isArray(body.Env) && !body.Env.length)) ||
          (body.DetachKeys !== undefined && body.DetachKeys !== '') ||
          body.AttachStdin !== true ||
          body.AttachStdout !== true ||
          body.AttachStderr !== true ||
          !Array.isArray(body.Cmd) ||
          !body.Cmd.length ||
          body.Cmd.some((part) => typeof part !== 'string' || part.includes('\0'))
        )
          return denied(response);
        if (create[1] === browser) {
          if (
            body.User !== 'node' ||
            (body.WorkingDir !== undefined && body.WorkingDir !== '') ||
            JSON.stringify(body.Cmd) !== JSON.stringify(['node', '/app/bridge.mjs'])
          )
            return denied(response);
        } else if (body.User === 'node') {
          if (!/^\/workspace\/[0-9a-f-]{36}$/.test(body.WorkingDir || '')) return denied(response);
        } else if (!/^[2-5]\d{4}:1002$/.test(body.User || '') || body.WorkingDir !== '/var/tmp')
          return denied(response);
        creating = true;
      } else if (execute && executions.has(execute[1])) {
        if (execute[2] === 'start' && method === 'POST') {
          body = await readJson(request);
          if (
            Object.keys(body).some((key) => !['Detach', 'Tty'].includes(key)) ||
            body.Detach !== false ||
            body.Tty !== false
          )
            return denied(response);
        } else if (execute[2] !== 'json' || method !== 'GET') return denied(response);
      } else return denied(response);
      const bytes = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
      const headers = bytes
        ? { 'Content-Type': 'application/json', 'Content-Length': bytes.length }
        : {};
      if (!creating && method === 'POST') {
        headers.Connection = 'Upgrade';
        headers.Upgrade = 'tcp';
      }
      const upstream = http.request({ socketPath, path: request.url, method, headers });
      upstream.on('error', () => {
        if (!response.headersSent) response.writeHead(502).end('Runtime unavailable.');
        else response.destroy();
      });
      upstream.on('response', async (incoming) => {
        if (inspecting) {
          try {
            const result = await readJson(incoming);
            if (incoming.statusCode !== 200) return response.writeHead(502).end();
            response.writeHead(200, { 'Content-Type': 'application/json' });
            response.end(
              JSON.stringify({
                Id: inspecting,
                Name: `/${inspecting}`,
                Config: { Tty: false },
                State: {
                  Running: result.State?.Running === true,
                  Paused: result.State?.Paused === true,
                  Restarting: result.State?.Restarting === true,
                },
              }),
            );
          } catch {
            response.writeHead(502).end('Runtime unavailable.');
          }
          return;
        }
        if (!creating) {
          response.writeHead(incoming.statusCode || 502, {
            'Content-Type': incoming.headers['content-type'] || 'application/json',
          });
          incoming.pipe(response);
          return;
        }
        try {
          const result = await readJson(incoming);
          if (incoming.statusCode === 201 && /^[a-f0-9]{64}$/.test(result.Id)) {
            executions.set(result.Id, Date.now());
            response.writeHead(201, { 'Content-Type': 'application/json' });
            response.end(JSON.stringify({ Id: result.Id }));
          } else response.writeHead(502).end('Runtime execution could not start.');
        } catch {
          response.writeHead(502).end('Runtime execution could not start.');
        }
      });
      upstream.on('upgrade', (incoming, socket, head) => {
        const downstream = response.socket;
        if (!downstream || incoming.statusCode !== 101) return socket.destroy();
        downstream.write(
          'HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n',
        );
        if (head.length) downstream.write(head);
        socket.pipe(downstream);
        downstream.pipe(socket);
        socket.on('error', () => downstream.destroy());
        downstream.on('error', () => socket.destroy());
        socket.on('close', () => {
          if (!socket.readableEnded) downstream.destroy();
        });
        downstream.on('close', () => socket.destroy());
        request.resume();
      });
      response.on('close', () => upstream.destroy());
      upstream.end(bytes);
    } catch {
      if (!response.headersSent) response.writeHead(400).end('Invalid runtime request.');
    }
  });
  gateway.headersTimeout = 10_000;
  gateway.requestTimeout = 30_000;
  gateway.maxHeadersCount = 32;
  return gateway;
}

async function readJson(stream) {
  const chunks = [];
  let length = 0;
  for await (const chunk of stream) {
    length += chunk.length;
    if (length > 65_536) throw new Error('Request too large.');
    chunks.push(chunk);
  }
  const value = JSON.parse(Buffer.concat(chunks).toString());
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid body.');
  return value;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  createRuntimeProxy().listen(2375, '0.0.0.0');
