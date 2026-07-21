import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import { initWebMCP } from './webmcp.js';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// Expose site tools to in-browser AI agents that support WebMCP (no-op otherwise).
initWebMCP();
