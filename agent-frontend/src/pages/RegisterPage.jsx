import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Form, Input, Button, Card, Typography, message as antMsg } from 'antd'
import { UserOutlined, MailOutlined, LockOutlined } from '@ant-design/icons'
import { useAuth } from '../hooks/useAuth'

const { Text, Title } = Typography

export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)

  const onFinish = async (values) => {
    setLoading(true)
    const res = await register(values.username, values.email, values.password)
    setLoading(false)
    if (res.success) {
      antMsg.success('注册成功，请登录')
      navigate('/login')
    } else {
      antMsg.error(res.message || '注册失败')
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: 'linear-gradient(135deg, #f5f7fa 0%, #e6f0ff 100%)',
    }}>
      <Card style={{ width: 420, borderRadius: 16, boxShadow: '0 4px 24px rgba(0,0,0,.08)' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={3} style={{ color: '#1677ff', marginBottom: 4 }}>📝 创建账号</Title>
          <Text type="secondary">注册后可创建和管理自己的工单</Text>
        </div>

        <Form onFinish={onFinish} size="large">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }, { min: 2, message: '至少 2 个字符' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>
          <Form.Item name="email" rules={[
            { required: true, message: '请输入邮箱' },
            { type: 'email', message: '邮箱格式不正确' },
          ]}>
            <Input prefix={<MailOutlined />} placeholder="邮箱（如 name@example.com）" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '至少 6 位' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码（至少 6 位）" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading} style={{ borderRadius: 8, height: 44 }}>
              注册
            </Button>
          </Form.Item>
        </Form>

        <div style={{ textAlign: 'center' }}>
          <Text type="secondary">已有账号？</Text>
          <Link to="/login" style={{ marginLeft: 4, color: '#1677ff' }}>立即登录</Link>
        </div>
      </Card>
    </div>
  )
}