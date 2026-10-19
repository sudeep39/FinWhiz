import { createApplication } from './lib/web-server.mjs';
export const server = await createApplication();
server.listen(Number(process.env.PORT || 3100), process.env.HOST || '0.0.0.0', () => {
  console.log(`Finwhiz: http://localhost:${server.address().port}`);
});
