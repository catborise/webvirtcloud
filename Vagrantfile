# Two disposable compute nodes for the live tests (README: Running tests), so
# that migration runs between them: AlmaLinux 10 with nested KVM, set up by
# dev/libvirt-bootstrap.sh, with a SASL user for TCP and the host user's SSH
# key for root (SASL user wvc, password wvc-test).

Vagrant.configure(2) do |config|
  config.vm.box = "almalinux/10"
  %w[compute1 compute2].each do |name|
    config.vm.define(name) { |node| node.vm.hostname = "wvc-#{name}" }
  end
  config.vm.synced_folder ".", "/vagrant", disabled: true
  config.vm.provider :libvirt do |v|
    v.memory = 4096
    v.cpus = 2
    v.nested = true
    v.cpu_mode = "host-passthrough"
    v.video_type = "virtio"  # EL10 hosts have no cirrus
  end
  # the bootstrap installs this checkout's gstfsd
  config.vm.provision "file", source: "dev", destination: "/tmp/wvc/dev"
  config.vm.provision "file", source: "conf", destination: "/tmp/wvc/conf"
  config.vm.provision "file", source: "~/.ssh/id_ed25519.pub", destination: "/tmp/wvc/key.pub"
  config.vm.provision "shell", inline: <<-SHELL
    set -e
    sh /tmp/wvc/dev/libvirt-bootstrap.sh
    virsh pool-info default > /dev/null 2>&1 || { virsh pool-define-as default dir --target /var/lib/libvirt/images && virsh pool-autostart default && virsh pool-start default; }
    echo wvc-test | saslpasswd2 -a libvirt -p wvc
    install -d -m 700 /root/.ssh
    cat /tmp/wvc/key.pub >> /root/.ssh/authorized_keys
  SHELL
end
