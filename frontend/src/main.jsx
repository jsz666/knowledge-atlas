import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import { ConfirmProvider } from './components/Confirm.jsx';
import { ToastProvider } from './components/Toast.jsx';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ToastProvider>
      <ConfirmProvider>
        <App />
      </ConfirmProvider>
    </ToastProvider>
  </React.StrictMode>
);
