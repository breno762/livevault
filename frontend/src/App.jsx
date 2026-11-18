import { useEffect, useMemo, useState } from 'react'
import {
  Activity, Archive, Bell, CheckCircle2, ChevronDown, CircleHelp,
  Clapperboard, Cloud, Database, Download, HardDrive, LayoutDashboard,
  LifeBuoy, ListVideo, Menu, MonitorPlay, Plus, Radio, Search, Settings,
  ShieldCheck, SlidersHorizontal, Twitch, Video, Youtube, Zap
} from 'lucide-react'
import { api } from './api'

const demoChannels = [
  { id: '1', display_name: 'Gaules', username: 'gaules', platform: 'twitch', is_live: true, enabled: true },
  { id: '2', display_name: 'Alanzoka', username: 'alanzoka', platform: 'twitch', is_live: true, enabled: true },
  { id: '3', display_name: 'Canal de gameplay', username: 'gameplaybr', platform: 'youtube', is_live: false, enabled: true },
  { id: '4', display_name: 'Streamer Kick', username: 'streamerbr', platform: 'kick', is_live: false, enabled: true },
]
const demoRecordings = [
  { id: 'r1', channel_name: 'Gaules', platform: 'twitch', title: 'Live de CS2 — classificatória', status: 'recording', started_at: new Date(Date.now()-52*60000).toISOString(), duration_seconds: 3120, size_bytes: 4.2e9 },
  { id: 'r2', channel_name: 'Alanzoka', platform: 'twitch', title: 'Jogando com a galera', status: 'recording', started_at: new Date(Date.now()-28*60000).toISOString(), duration_seconds: 1680, size_bytes: 2.1e9 },
  { id: 'r3', channel_name: 'Canal de gameplay', platform: 'youtube', title: 'Live de Minecraft', status: 'completed', started_at: new Date(Date.now()-86400000).toISOString(), duration_seconds: 14400, size_bytes: 8.6e9 },
  { id: 'r4', channel_name: 'Streamer Kick', platform: 'kick', title: 'Just Chatting', status: 'completed', started_at: new Date(Date.now()-2*86400000).toISOString(), duration_seconds: 7200, size_bytes: 5.4e9 },
]
const platformLabel = { twitch: 'Twitch', youtube: 'YouTube', kick: 'Kick' }
const fmtBytes = n => {
  if (!n) return '—'
  const units = ['B','KB','MB','GB','TB']; let i = 0
  while (n >= 1024 && i < units.length-1) { n /= 1024; i++ }
  return `${n.toFixed(1)} ${units[i]}`
}
const fmtDuration = n => {
  if (!n) return '—'
  const h = Math.floor(n/3600), m = Math.floor((n%3600)/60)
  return h ? `${h}h ${m}min` : `${m}min`
}
function Platform({ name }) {
  if (name === 'youtube') return <Youtube size={15} />
  if (name === 'kick') return <span className="kick-mark">K</span>
  return <Twitch size={15} />
}
function App() {
  const [page, setPage] = useState('Dashboard')
  const [channels, setChannels] = useState(demoChannels)
  const [recordings, setRecordings] = useState(demoRecordings)
  const [dashboard, setDashboard] = useState(null)
  const [connected, setConnected] = useState(false)
  const [search, setSearch] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [platform, setPlatform] = useState('twitch')
  const [username, setUsername] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let alive = true
    Promise.all([api.dashboard(), api.channels(), api.recordings()])
      .then(([d,c,r]) => {
        if (!alive) return
        setDashboard(d); setChannels(c); setRecordings(r); setConnected(true)
      })
      .catch(() => { if (alive) setConnected(false) })
    return () => { alive = false }
  }, [])

  const liveCount = channels.filter(c => c.is_live).length
  const activeCount = recordings.filter(r => r.status === 'recording').length
  const totalStorage = dashboard?.storage_used_bytes ?? recordings.reduce((a,r) => a+(r.size_bytes||0),0)
  const filteredChannels = useMemo(() => channels.filter(c =>
    `${c.display_name} ${c.username} ${c.platform}`.toLowerCase().includes(search.toLowerCase())
  ), [channels, search])
  const filteredRecordings = useMemo(() => recordings.filter(r =>
    `${r.channel_name} ${r.title} ${r.platform}`.toLowerCase().includes(search.toLowerCase())
  ), [recordings, search])

  async function addChannel(e) {
    e.preventDefault()
    if (!username.trim()) return
    setBusy(true)
    try {
      if (connected) {
        const created = await api.createChannel(platform, username.trim())
        setChannels(prev => [created, ...prev])
      } else {
        setChannels(prev => [{ id: String(Date.now()), username: username.trim(), display_name: username.trim(), platform, is_live: false, enabled: true }, ...prev])
        setNotice('Canal adicionado apenas à demonstração. Conecte o backend para salvar de verdade.')
      }
      setUsername(''); setShowAdd(false)
    } catch {
      setNotice('Não foi possível cadastrar. Verifique a API do backend.')
    } finally { setBusy(false) }
  }

  const nav = [
    { label: 'Dashboard', icon: LayoutDashboard },
    { label: 'Canais monitorados', icon: Radio },
    { label: 'Gravações', icon: Video },
    { label: 'Biblioteca', icon: Archive },
    { label: 'Aprovações', icon: ShieldCheck },
  ]
  const title = page === 'Dashboard' ? 'Visão geral' : page
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon"><Clapperboard size={22}/></div><div><b>livevault</b><small>LIVEVAULT</small></div></div>
      <div className="workspace"><div className="workspace-avatar">C</div><div><b>Meu espaço</b><small>Plano pessoal</small></div><ChevronDown size={15}/></div>
      <div className="nav-caption">MENU PRINCIPAL</div>
      <nav>{nav.map(item => <button key={item.label} className={`nav-item ${page===item.label?'active':''}`} onClick={()=>{setPage(item.label);setSearch('')}}><item.icon size={18}/><span>{item.label}</span>{item.label==='Aprovações'&&<em>0</em>}</button>)}</nav>
      <div className="nav-caption second">GERENCIAMENTO</div>
      <nav>
        <button className={`nav-item ${page==='Armazenamento'?'active':''}`} onClick={()=>setPage('Armazenamento')}><HardDrive size={18}/>Armazenamento</button>
        <button className={`nav-item ${page==='Atividade'?'active':''}`} onClick={()=>setPage('Atividade')}><Activity size={18}/>Atividade</button>
        <button className={`nav-item ${page==='Configurações'?'active':''}`} onClick={()=>setPage('Configurações')}><Settings size={18}/>Configurações</button>
      </nav>
      <div className="sidebar-bottom"><div className="help-card"><div className="help-icon"><LifeBuoy size={18}/></div><b>Precisa de ajuda?</b><p>Consulte a documentação do LiveVault.</p><button onClick={()=>setNotice('A documentação será adicionada nesta seção.')}>Central de ajuda <span>↗</span></button></div><div className="profile"><div className="profile-avatar">U</div><div><b>Usuário</b><small>Administrador</small></div><Settings size={16}/></div></div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="breadcrumb"><span>LiveVault</span><b>/</b>{title}</div><div className="top-actions"><div className={`connection ${connected?'online':'demo'}`}><i/>{connected?'Backend conectado':'Modo demonstração'}</div><button className="icon-button" title="Notificações"><Bell size={18}/></button><div className="top-avatar">U</div></div></header>
      <div className="content">
        <div className="page-heading"><div><div className="eyebrow">CENTRAL DE CONTROLE</div><h1>{title}</h1><p>Acompanhe seus canais e gravações em um só lugar.</p></div><div className="heading-actions"><button className="button secondary" onClick={()=>setPage('Canais monitorados')}><Radio size={16}/> Ver canais</button><button className="button primary" onClick={()=>setShowAdd(true)}><Plus size={17}/> Adicionar canal</button></div></div>
        {notice && <div className="notice" onClick={()=>setNotice('')}>{notice}<button>×</button></div>}
        {page==='Dashboard' && <>
          <div className="stats-grid">
            <Stat icon={<Radio/>} label="Canais monitorados" value={channels.length} detail="Em todas as plataformas" />
            <Stat icon={<MonitorPlay/>} label="Canais ao vivo" value={liveCount} detail="Detectados pelo monitoramento" accent />
            <Stat icon={<Video/>} label="Gravações ativas" value={activeCount} detail="Capturas em andamento" />
            <Stat icon={<Database/>} label="Armazenamento usado" value={fmtBytes(totalStorage)} detail="Arquivos registrados" />
          </div>
          <section className="section-card"><div className="section-head"><div><h2>Gravações em andamento</h2><p>Transmissões que estão sendo capturadas agora.</p></div><button className="text-button" onClick={()=>setPage('Gravações')}>Ver todas <span>→</span></button></div><RecordingTable rows={recordings.filter(r=>r.status==='recording')} /></section>
          <section className="section-card"><div className="section-head"><div><h2>Canais monitorados</h2><p>Seus canais cadastrados nas plataformas.</p></div><button className="text-button" onClick={()=>setPage('Canais monitorados')}>Gerenciar canais <span>→</span></button></div><ChannelGrid channels={channels.slice(0,4)}/></section>
        </>}
        {page==='Canais monitorados' && <section className="section-card"><div className="section-head"><div><h2>Seus canais</h2><p>Gerencie os canais que o sistema deve acompanhar.</p></div><button className="button primary small" onClick={()=>setShowAdd(true)}><Plus size={15}/> Adicionar canal</button></div><div className="toolbar"><div className="search"><Search size={16}/><input placeholder="Buscar canal ou plataforma..." value={search} onChange={e=>setSearch(e.target.value)}/></div><button className="button secondary small"><SlidersHorizontal size={15}/> Filtros</button></div><ChannelGrid channels={filteredChannels}/></section>}
        {page==='Gravações' && <section className="section-card"><div className="section-head"><div><h2>Todas as gravações</h2><p>Histórico de capturas iniciadas pelo monitoramento.</p></div></div><div className="toolbar"><div className="search"><Search size={16}/><input placeholder="Buscar gravação..." value={search} onChange={e=>setSearch(e.target.value)}/></div></div><RecordingTable rows={filteredRecordings}/></section>}
        {page==='Biblioteca' && <section className="section-card empty-state"><div className="empty-icon"><ListVideo size={28}/></div><h2>Biblioteca de vídeos</h2><p>As gravações concluídas e os clipes processados aparecerão aqui quando o backend disponibilizar os arquivos.</p></section>}
        {page==='Aprovações' && <section className="section-card empty-state"><div className="empty-icon"><ShieldCheck size={28}/></div><h2>Nenhum conteúdo aguardando aprovação</h2><p>Os clipes gerados serão exibidos aqui. Nenhuma publicação será feita sem sua autorização.</p></section>}
        {page==='Armazenamento' && <section className="section-card"><div className="section-head"><div><h2>Armazenamento</h2><p>Uso estimado com base nos arquivos conhecidos.</p></div></div><div className="storage-panel"><div className="storage-icon"><HardDrive/></div><div><b>{fmtBytes(totalStorage)}</b><small>Espaço registrado</small></div></div><div className="progress"><span style={{width:'34%'}}/></div><p className="muted">Limite e capacidade total serão informados pelo backend.</p></section>}
        {page==='Atividade' && <section className="section-card empty-state"><div className="empty-icon"><Activity/></div><h2>Histórico de atividade</h2><p>Eventos de gravação, reconexões e alertas aparecerão aqui.</p></section>}
        {page==='Configurações' && <section className="section-card"><div className="section-head"><div><h2>Configurações do sistema</h2><p>Preferências de monitoramento e gravação.</p></div></div><div className="settings-row"><div><b>Gravação automática</b><small>Iniciar a captura quando um canal monitorado entrar ao vivo.</small></div><span className="status-pill success">Ativada pelo backend</span></div><div className="settings-row"><div><b>Segmentos de gravação</b><small>Divisão em partes de 30 segundos para recuperação.</small></div><span className="status-pill">30 segundos</span></div><div className="settings-row"><div><b>Aprovação antes de publicar</b><small>Exigir aprovação manual para qualquer publicação.</small></div><span className="status-pill success">Obrigatória</span></div></section>}
      </div>
    </main>
    {showAdd && <div className="modal-backdrop" onMouseDown={e=>e.target===e.currentTarget&&setShowAdd(false)}><form className="modal" onSubmit={addChannel}><div className="modal-head"><div><h2>Adicionar canal</h2><p>Escolha a plataforma e informe o nome do canal.</p></div><button type="button" className="icon-button" onClick={()=>setShowAdd(false)}>×</button></div><label>Plataforma<select value={platform} onChange={e=>setPlatform(e.target.value)}><option value="twitch">Twitch</option><option value="youtube">YouTube</option><option value="kick">Kick</option></select></label><label>Nome ou identificador do canal<input autoFocus value={username} onChange={e=>setUsername(e.target.value)} placeholder="Ex.: nome_do_canal" required/></label><div className="modal-actions"><button type="button" className="button secondary" onClick={()=>setShowAdd(false)}>Cancelar</button><button className="button primary" disabled={busy}>{busy?'Salvando...':'Adicionar canal'}</button></div><small className="modal-foot">No modo demonstração, o cadastro fica apenas nesta sessão. Com o backend conectado, será salvo permanentemente.</small></form></div>}
  </div>
}
function Stat({icon,label,value,detail,accent}) { return <div className="stat-card"><div className={`stat-icon ${accent?'purple':''}`}>{icon}</div><div className="stat-label">{label}</div><div className="stat-value">{value}</div><div className="stat-detail">{detail}</div></div> }
function ChannelGrid({channels}) {
  if (!channels.length) return <div className="empty-inline">Nenhum canal encontrado.</div>
  return <div className="channel-grid">{channels.map(c=><div className="channel-card" key={c.id}><div className={`platform-icon ${c.platform}`}><Platform name={c.platform}/></div><div className="channel-info"><b>{c.display_name||c.username}</b><small>@{c.username} · {platformLabel[c.platform]||c.platform}</small></div><span className={`live-tag ${c.is_live?'live':'offline'}`}><i/>{c.is_live?'AO VIVO':'OFFLINE'}</span></div>)}</div>
}
function RecordingTable({rows}) {
  if (!rows.length) return <div className="empty-inline">Nenhuma gravação encontrada.</div>
  return <div className="table-wrap"><table><thead><tr><th>CANAL / CONTEÚDO</th><th>PLATAFORMA</th><th>STATUS</th><th>DURAÇÃO</th><th>TAMANHO</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}><td><div className="recording-name"><div className="recording-thumb"><Video size={17}/></div><div><b>{r.channel_name}</b><small>{r.title||'Transmissão ao vivo'}</small></div></div></td><td><span className="platform-name"><Platform name={r.platform}/>{platformLabel[r.platform]||r.platform}</span></td><td><span className={`status-pill ${r.status==='recording'?'recording':r.status==='completed'?'success':''}`}>{r.status==='recording'?'Gravando':r.status==='completed'?'Concluída':r.status}</span></td><td>{fmtDuration(r.duration_seconds)}</td><td>{fmtBytes(r.size_bytes)}</td></tr>)}</tbody></table></div>
}
export default App
