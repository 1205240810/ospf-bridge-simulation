from driver import run_cmd

class Bridge:
    """
    使用 Open vSwitch 实现的网桥
    """
    def __init__(self, name):
        self.name = name

    def create(self):
        # OVS 创建网桥: ovs-vsctl add-br <name>
        # --may-exist 表示如果已存在就不报错 (幂等性)
        run_cmd(f"ovs-vsctl --may-exist add-br {self.name}")
        # 启动网桥接口 (OVS 建好后，Linux 系统里也会多一个同名网卡，必须 up 起来)
        run_cmd(f"ip link set dev {self.name} up")

    def destroy(self):
        # OVS 删除网桥: ovs-vsctl del-br <name>
        # --if-exists 防止报错
        run_cmd(f"ovs-vsctl --if-exists del-br {self.name}")

    def add_port(self, interface_name):
        """将接口加入 OVS 网桥"""
        # ovs-vsctl add-port <br_name> <if_name>
        run_cmd(f"ovs-vsctl --may-exist add-port {self.name} {interface_name}")

    def del_port(self, interface_name):
        """将接口从 OVS 网桥移除"""
        # ovs-vsctl del-port <br_name> <if_name>
        run_cmd(f"ovs-vsctl --if-exists del-port {self.name} {interface_name}")
        
    def bind_physical_interface(self, physical_if_name):
        """[半实物] 绑定物理网卡"""
        print(f"🔗 OVS 正在桥接物理网卡: {physical_if_name} -> {self.name}")
        run_cmd(f"ip link set dev {physical_if_name} up")
        # 清空物理网卡 IP (防止冲突，建议根据实际情况决定是否保留)
        # run_cmd(f"ip addr flush dev {physical_if_name}") 
        self.add_port(physical_if_name)

class TapInterface:
    def __init__(self, name):
        self.name = name
        self.current_bridge = None

    def create(self):
        run_cmd(f"ip tuntap add dev {self.name} mode tap")
        run_cmd(f"ip link set dev {self.name} up")
        run_cmd(f"ip link set dev {self.name} promisc on")

    def destroy(self):
        # 如果当前插在 OVS 上，先从 OVS 拔下来
        self.unplug()
        run_cmd(f"ip link del {self.name} 2>/dev/null")

    def plug_into(self, bridge: Bridge):
        """插线操作"""
        self.unplug() # 先拔再插
        
        # 调用 OVS 的 add-port
        bridge.add_port(self.name)
        
        self.current_bridge = bridge
        print(f"🔗 [连接成功] {self.name} <---> {bridge.name} (OVS)")

    def unplug(self):
        """拔线操作"""
        if self.current_bridge:
            # 调用 OVS 的 del-port
            self.current_bridge.del_port(self.name)
            print(f"🔌 [断开连接] {self.name} X {self.current_bridge.name}")
            self.current_bridge = None

def create_veth_link(bridge_a_name, bridge_b_name):
    """
    创建 veth pair 连接两个 OVS 网桥 (Router 连 Router)
    """
    import random
    suffix = random.randint(1000, 9999)
    veth_a = f"veth_a_{suffix}"
    veth_b = f"veth_b_{suffix}"

    print(f"🔗 [OVS级联] 正在连接: {bridge_a_name} <==> {bridge_b_name}")

    # 1. 创建 Linux veth pair (这步不变)
    run_cmd(f"ip link add {veth_a} type veth peer name {veth_b}")
    run_cmd(f"ip link set dev {veth_a} up")
    run_cmd(f"ip link set dev {veth_b} up")
    
    # 2. 插线 (使用 ovs-vsctl)
    run_cmd(f"ovs-vsctl --may-exist add-port {bridge_a_name} {veth_a}")
    run_cmd(f"ovs-vsctl --may-exist add-port {bridge_b_name} {veth_b}")
