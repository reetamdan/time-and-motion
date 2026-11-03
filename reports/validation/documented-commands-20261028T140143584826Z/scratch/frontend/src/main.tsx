import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './design-system/tokens.css';
import './design-system/base.css';
import './design-system/components.css';
import './app.css';
import App from './App';

// Theme before first paint (per-viewer preference; dark operations theme by default).
try {
  if (window.localStorage.getItem('aegis-theme') === 'light') document.documentElement.dataset.theme = 'light';
} catch { /* storage unavailable: keep the default */ }

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
