// Executado uma vez antes do Yuvomi subir (serviço rekey do docker-compose).
let D; try { D = require('better-sqlite3-multiple-ciphers'); } catch (e) { console.log('[rekey] imagem sem cifra, nada a fazer'); process.exit(0); }
const fs = require('fs');
const P = process.env.DB_PATH || '/data/yuvomi.db';
const B = process.env.BACKUP_DIR || '/backups';
const NEW = process.env.NEW_KEY, OLD = process.env.OLD_KEY;
const hex = k => Buffer.from(k, 'utf8').toString('hex');
function open(k) {
  const db = new D(P);
  db.pragma("cipher = 'sqlcipher'");
  db.pragma(`key="x'${hex(k)}'"`);
  try { db.prepare('SELECT count(*) FROM sqlite_master').get(); return db; }
  catch (e) { db.close(); return null; }
}
if (!NEW || !OLD) { console.error('[rekey] faltam chaves'); process.exit(1); }
if (!fs.existsSync(P)) { console.log('[rekey] sem banco, nada a fazer'); process.exit(0); }
let db = open(NEW);
if (db) { db.close(); console.log('[rekey] já está na chave nova'); process.exit(0); }
db = open(OLD);
if (!db) { console.error('[rekey] nenhuma chave abre o banco; não mexi em nada'); process.exit(1); }
const [ck] = db.pragma('wal_checkpoint(TRUNCATE)');
if (ck && ck.busy) { console.error('[rekey] WAL ocupado; não mexi em nada'); process.exit(1); }
db.pragma('journal_mode = DELETE');
db.close();
const ts = new Date().toISOString().replace(/[:.]/g, '-');
fs.mkdirSync(B, { recursive: true });
fs.copyFileSync(P, `${B}/pre-rekey-${ts}.db`);
db = open(OLD);
db.pragma(`rekey="x'${hex(NEW)}'"`);
db.close();
db = open(NEW);
if (!db) { console.error('[rekey] verificação falhou; restaure ' + `${B}/pre-rekey-${ts}.db`); process.exit(1); }
const n = db.prepare('SELECT count(*) c FROM sqlite_master').get().c;
db.pragma('journal_mode = WAL'); db.close();
console.log(`[rekey] ok, ${n} objetos; cópia antiga em ${B}/pre-rekey-${ts}.db`);
