import { mkdir, readFile, writeFile, rename, unlink } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { resolve } from 'node:path';

// One dedicated app instance owns the queue. Use a persistent directory in
// hosted environments; ephemeral deployment disks do not survive redeployment.
export class Storage {
  constructor({ directory = process.env.FINWHIZ_DATA_DIR || '.runtime' } = {}) {
    this.directory = resolve(directory);
  }
  async init() {
    await mkdir(this.directory, { recursive: true });
    return this;
  }
  path(key) { return resolve(this.directory, createHash('sha256').update(key).digest('hex')); }
  async get(key) {
    try { return await readFile(this.path(key)); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }
  async put(key, value) {
    const bytes = Buffer.isBuffer(value) ? value : Buffer.from(value);
    const temp = `${this.path(key)}.${randomUUID()}.tmp`;
    await writeFile(temp, bytes);
    await rename(temp, this.path(key));
  }
  async json(key, fallback = null) { const data = await this.get(key); return data ? JSON.parse(data) : fallback; }
  async putJson(key, value) { await this.put(key, JSON.stringify(value)); }
  async remove(key) { try { await unlink(this.path(key)); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
  async close() { /* Files have no persistent connections to release. */ }
}
