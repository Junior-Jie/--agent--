import { useState, useEffect } from 'react'
import { Table, Tag, Button, Modal, Form, Input, Select, Space, Typography, message as antMsg, Popconfirm } from 'antd'
import { PlusOutlined, ReloadOutlined, EyeOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { getTickets, createTicketApi, updateTicketApi, deleteTicketApi } from '../api'
import { useAuth } from '../hooks/useAuth'

const { Text, Title } = Typography
const { TextArea } = Input

const PRIORITY_COLORS = { '紧急': 'red', '高': 'orange', '中': 'blue', '低': 'green' }
const STATUS_COLORS = { '待处理': 'default', '处理中': 'processing', '待确认': 'warning', '已完成': 'success', '已关闭': 'error' }

export default function TicketsPage() {
  const { user } = useAuth()
  const [tickets, setTickets] = useState([])
  const [loading, setLoading] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [selected, setSelected] = useState(null)
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()
  const [createForm] = Form.useForm()

  const fetchTickets = async () => {
    setLoading(true)
    try {
      const res = await getTickets()
      setTickets(res.tickets || [])
    } catch (e) {
      if (e?.response?.status === 401 || e?.response?.status === 403) {
        antMsg.info('请登录后查看工单')
      } else {
        antMsg.error('加载工单失败')
      }
    }
    finally { setLoading(false) }
  }

  useEffect(() => { fetchTickets() }, [])

  // 创建
  const handleCreate = async (values) => {
    try {
      await createTicketApi(values.title, values.priority, values.description)
      antMsg.success('工单创建成功')
      setCreateOpen(false)
      createForm.resetFields()
      fetchTickets()
    } catch (e) { antMsg.error(e.response?.data?.detail || '创建失败') }
  }

  // 更新
  const handleEdit = async (values) => {
    if (!selected) return
    try {
      await updateTicketApi(selected.ticket_no, values)
      antMsg.success('更新成功')
      setEditOpen(false)
      fetchTickets()
    } catch (e) { antMsg.error(e.response?.data?.detail || '更新失败') }
  }

  // 关闭（软删除）
  const handleClose = async (ticketNo) => {
    try {
      await deleteTicketApi(ticketNo)
      antMsg.success('已关闭')
      fetchTickets()
    } catch (e) { antMsg.error(e.response?.data?.detail || '操作失败') }
  }

  // 彻底删除（仅管理员）
  const handleHardDelete = async (ticketNo) => {
    try {
      await deleteTicketApi(ticketNo, true)
      antMsg.success('已彻底删除')
      fetchTickets()
    } catch (e) { antMsg.error(e.response?.data?.detail || '操作失败') }
  }


  const columns = [
    {
      title: '工单号', dataIndex: 'ticket_no', key: 'ticket_no', width: 100,
      render: (v) => <Text code style={{ color: '#1677ff' }}>{v}</Text>,
    },
    {
      title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (v, r) => <a onClick={() => { setSelected(r); setDetailOpen(true) }}>{v}</a>,
    },
    {
      title: '优先级', dataIndex: 'priority', key: 'priority', width: 80,
      render: (v) => <Tag color={PRIORITY_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v) => <Tag color={STATUS_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: '负责人', dataIndex: 'assignee', key: 'assignee', width: 90,
    },
    {
      title: '提交人', dataIndex: 'username', key: 'username', width: 90,
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 110,
    },
    {
      title: '操作', key: 'actions', width: 200,
      render: (_, r) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EyeOutlined />}
            onClick={() => { setSelected(r); setDetailOpen(true) }}>详情</Button>
          {user?.role === 'admin' && (
            <Button type="link" size="small" icon={<EditOutlined />}
              onClick={() => { setSelected(r); editForm.setFieldsValue({ status: r.status, assignee: r.assignee }); setEditOpen(true) }}
              disabled={r.status === '已关闭'}>编辑</Button>
          )}
          <Popconfirm title="确定关闭此工单？" onConfirm={() => handleClose(r.ticket_no)}
            disabled={r.status === '已关闭'}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}
              disabled={r.status === '已关闭'}>关闭</Button>
          </Popconfirm>
          {user?.role === 'admin' && (
            <Popconfirm title="彻底删除无法恢复，确定？" onConfirm={() => handleHardDelete(r.ticket_no)}>
              <Button type="link" size="small" danger>🗑️</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24 }}>
      {/* 顶栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <Title level={4} style={{ margin: 0, color: '#1677ff' }}>
            <Link to="/chat" style={{ fontSize: 14, marginRight: 12, color: '#999' }}>{'<'} 对话</Link>
            工单管理
          </Title>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchTickets} loading={loading}>刷新</Button>
          {user ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建工单</Button>
          ) : (
            <Link to="/login"><Button type="primary">登录后创建工单</Button></Link>
          )}
        </Space>
      </div>

      {/* 表格 */}
      <Table
        columns={columns}
        dataSource={tickets}
        rowKey="ticket_no"
        loading={loading}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: false }}
      />

      {/* 详情弹窗 */}
      <Modal title="工单详情" open={detailOpen} onCancel={() => setDetailOpen(false)} footer={null} width={560}>
        {selected && (
          <div style={{ lineHeight: 2 }}>
            <p><Text strong>工单号：</Text><Text code>{selected.ticket_no}</Text></p>
            <p><Text strong>标题：</Text>{selected.title}</p>
            <p><Text strong>优先级：</Text><Tag color={PRIORITY_COLORS[selected.priority]}>{selected.priority}</Tag></p>
            <p><Text strong>状态：</Text><Tag color={STATUS_COLORS[selected.status]}>{selected.status}</Tag></p>
            <p><Text strong>负责人：</Text>{selected.assignee}</p>
            <p><Text strong>提交人：</Text>{selected.username}</p>
            <p><Text strong>SLA：</Text>{selected.sla_hours || '-'} 小时</p>
            <p><Text strong>描述：</Text></p>
            <div style={{ background: '#f5f5f5', padding: 12, borderRadius: 8, whiteSpace: 'pre-wrap' }}>
              {selected.description || '无'}
            </div>
            <p style={{ marginTop: 12 }}><Text type="secondary">创建: {selected.created_at} | 更新: {selected.updated_at}</Text></p>
          </div>
        )}
      </Modal>

      {/* 创建弹窗 */}
      <Modal title="创建工单" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => createForm.submit()} okText="创建">
        <Form form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="title" label="标题" rules={[{ required: true, min: 2 }]}>
            <Input placeholder="简述问题" />
          </Form.Item>
          <Form.Item name="priority" label="优先级" initialValue="中" rules={[{ required: true }]}>
            <Select options={['紧急', '高', '中', '低'].map(v => ({ value: v }))} />
          </Form.Item>
          <Form.Item name="description" label="详细描述">
            <TextArea rows={3} placeholder="具体现象、复现步骤、影响范围" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal title={`编辑 ${selected?.ticket_no || ''}`} open={editOpen} onCancel={() => setEditOpen(false)} onOk={() => editForm.submit()} okText="保存">
        <Form form={editForm} layout="vertical" onFinish={handleEdit}>
          <Form.Item name="status" label="状态">
            <Select options={['待处理', '处理中', '待确认', '已完成', '已关闭'].map(v => ({ value: v }))} allowClear />
          </Form.Item>
          <Form.Item name="assignee" label="负责人">
            <Input placeholder="输入负责人姓名" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}