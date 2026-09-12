import { createGateway } from './gateway.js';
const token = process.env.JARVIS_GATEWAY_TOKEN;
if (!token) throw new Error('Set JARVIS_GATEWAY_TOKEN to a random secret of at least 32 characters. The gateway fails closed without it.');
const gateway = createGateway({ token });
const port = await gateway.listen(Number(process.env.JARVIS_GATEWAY_PORT ?? '4318'));
console.log(`JARVIS simulation gateway listening on http://127.0.0.1:${port}; all HTTP/WS endpoints require bearer authentication.`);
for (const event of ['SIGINT', 'SIGTERM'] as const) process.once(event, () => { void gateway.close().then(() => process.exit(0)); });
