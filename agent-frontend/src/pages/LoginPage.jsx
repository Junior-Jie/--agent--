import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Form, Input, Button, Card, Typography, message as antMsg } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useAuth } from '../hooks/useAuth'

const { Text, Title } = Typography

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)

  const onFinish = async (values) => {
    setLoading(true)
    const res = await login(values.username, values.password)
    setLoading(false)
    if (res.success) {
      antMsg.success(`欢迎回来，${res.user.display_name || res.user.username}`)
      navigate('/chat')
    } else {
      antMsg.error(res.message || '登录失败')
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: 'linear-gradient(135deg, #f5f7fa 0%, #e6f0ff 100%)',
    }}>
      <Card style={{ width: 400, borderRadius: 16, boxShadow: '0 4px 24px rgba(0,0,0,.08)' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={3} style={{ color: '#1677ff', marginBottom: 4 }}>🤖 智能客服系统</Title>
          <Text type="secondary">登录您的账号</Text>
        </div>

        <Form onFinish={onFinish} size="large">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading} style={{ borderRadius: 8, height: 44 }}>
              登录
            </Button>
          </Form.Item>
        </Form>

        <div style={{ textAlign: 'center' }}>
          <Text type="secondary">还没有账号？</Text>
          <Link to="/register" style={{ marginLeft: 4, color: '#1677ff' }}>立即注册</Link>
        </div>

        <div style={{ textAlign: 'center', marginTop: 16 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            游客可直接 <Link to="/chat">进入对话</Link>，无需登录
          </Text>
        </div>
      </Card>
    </div>
  )
}