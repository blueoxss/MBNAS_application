import { ArchiveCandidateModal } from './components/archive/ArchiveCandidateModal';
import { CandidateModal } from './components/CandidateModal';
import { Nav } from './components/Nav';
import { Archive } from './screens/Archive';
import { Compare } from './screens/Compare';
import { History } from './screens/History';
import { Lineage } from './screens/Lineage';
import { Monitor } from './screens/Monitor';
import { Server } from './screens/Server';
import { Settings } from './screens/Settings';
import { useApp } from './state/context';

export function App(): JSX.Element {
  const app = useApp();
  return (
    <div className="app">
      <Nav screen={app.screen} onSelect={app.setScreen} />
      <main className="main" id="content" tabIndex={-1}>
        {app.screen === 'archive' && <Archive />}
        {app.screen === 'compare' && <Compare />}
        {app.screen === 'monitor' && <Monitor />}
        {app.screen === 'settings' && <Settings />}
        {app.screen === 'lineage' && <Lineage />}
        {app.screen === 'history' && <History />}
        {app.screen === 'server' && <Server />}
      </main>
      <CandidateModal />
      {app.screen === 'archive' && <ArchiveCandidateModal />}
    </div>
  );
}
