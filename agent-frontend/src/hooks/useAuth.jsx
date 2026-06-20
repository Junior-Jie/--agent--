import { useState, useEffect, createContext, useContext } from 'react'
import { login as loginApi, register as registerApi, getMe } from '../api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [token, setToken] = useState(localStorage.getItem('token'))
  const [loading, setLoading] = useState(true)

  // 启动时校验 token 是否有效
  useEffect(() => {
    if (token) {
      getMe()
        .then(res => setUser(res.user))
        .catch(() => { localStorage.removeItem('token'); setToken('') })
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [token])

  const login = async (username, password) => {
    const res = await loginApi(username, password)
    if (res.success) {
      localStorage.setItem('token', res.token)
      setToken(res.token)
      setUser(res.user)
    }
    return res
  }

  const register = async (username, email, password) => {
    const res = await registerApi(username, email, password)
    return res
  }

  const logout = () => {
    localStorage.removeItem('token')
    setToken('')
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}