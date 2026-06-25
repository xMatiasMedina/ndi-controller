import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import RemotePage from './RemotePage.jsx';
import './styles/theme.css';

// Tiny path-based router: /remote is the simple cast remote, everything
// else is the full advanced console. No react-router dependency needed.
const path = window.location.pathname.replace(/\/+$/, '');
const Root = path === '/remote' ? RemotePage : App;

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
