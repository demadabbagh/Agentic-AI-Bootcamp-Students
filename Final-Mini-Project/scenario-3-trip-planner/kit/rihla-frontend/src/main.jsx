import MyTrips from './pages/MyTrips.jsx'
import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App.jsx'
import TripDashboard from './pages/TripDashboard.jsx'
import Login from './pages/Login.jsx'
import './index.css'

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <Routes>
      <Route path='/' element={<App />} />
      <Route path='/trip' element={<TripDashboard />} />
      <Route path="/login" element={<Login />} />
          <Route path="/my-trips" element={<MyTrips />} />
    </Routes>
  </BrowserRouter>
)
