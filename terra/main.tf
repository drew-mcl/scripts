# Configure the AWS Provider
provider "aws" {
  region = var.aws_region
}

# --- DATA SOURCES ---
# Find the VPC. If no ID is provided in variables, it gets the default VPC.
data "aws_vpc" "selected" {
  id      = var.vpc_id != "" ? var.vpc_id : null
  default = var.vpc_id != "" ? null : true
}

# Find all public subnets in the selected VPC. We need these for the NLB.
data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.selected.id]
  }
  filter {
    name   = "map-public-ip-on-launch"
    values = ["true"]
  }
}

# --- NEW: Find the provisioned Service Catalog Product ---
# This looks for a product named "ec2-spoke" and checks if its status is "AVAILABLE".
# If not found or the status is bad, Terraform will stop.
data "aws_servicecatalog_provisioned_product" "spoke" {
  accept_language = "en"
  
  filter {
    name = "Name"
    value = var.sc_product_name
  }

  lifecycle {
    postcondition {
      condition     = self.status == "AVAILABLE"
      error_message = "The Service Catalog product '${var.sc_product_name}' is not in a ready state. Current status: ${self.status}"
    }
  }
}

# --- NEW: Find the existing Border Control Security Group ---
data "aws_security_group" "border_control" {
  name   = var.existing_sg_name
  vpc_id = data.aws_vpc.selected.id
}


# --- KEY PAIR ---
# Upload your public key to AWS to allow SSH access to the EC2 instance.
resource "aws_key_pair" "generated_key" {
  key_name   = var.key_name
  public_key = file(var.public_key_path)
}

# --- SECURITY GROUPS ---
# Security Group for the Network Load Balancer.
# This remains the same. It is the internet-facing firewall.
resource "aws_security_group" "nlb_sg" {
  name        = "nlb-ssh-sg"
  description = "Allow SSH from my IP to NLB"
  vpc_id      = data.aws_vpc.selected.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "NLB-SSH-SecurityGroup" }
}

# Security Group for the EC2 Instance.
# It allows SSH traffic ONLY from within the VPC. This means only the NLB can reach it.
resource "aws_security_group" "ec2_sg" {
  name        = "ec2-instance-sg-for-nlb"
  description = "Allow SSH from within the VPC (for NLB)"
  vpc_id      = data.aws_vpc.selected.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.selected.cidr_block]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "EC2-Instance-NLB-SecurityGroup" }
}

# --- NETWORK LOAD BALANCER (NLB) ---
# This section remains unchanged.
resource "aws_lb" "ssh_nlb" {
  name               = "ssh-nlb"
  internal           = false
  load_balancer_type = "network"
  subnets            = data.aws_subnets.public.ids
  security_groups    = [aws_security_group.nlb_sg.id]
  tags = { Name = "SSH-NLB" }
}

resource "aws_lb_target_group" "ssh_tg" {
  name     = "ssh-nlb-tg"
  port     = 22
  protocol = "TCP"
  vpc_id   = data.aws_vpc.selected.id
  health_check { protocol = "TCP" }
  tags = { Name = "SSH-NLB-TargetGroup" }
}

resource "aws_lb_listener" "ssh_listener" {
  load_balancer_arn = aws_lb.ssh_nlb.arn
  port              = "22"
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ssh_tg.arn
  }
}

# --- EC2 INSTANCE (UPDATED) ---
resource "aws_instance" "web_server" {
  # MODIFIED: Use the AMI from the Service Catalog product's output
  ami = data.aws_servicecatalog_provisioned_product.spoke.outputs[var.sc_product_ami_output_key]

  instance_type = var.instance_type
  key_name      = aws_key_pair.generated_key.key_name

  # MODIFIED: Attach both the NLB security group AND the existing Border Control security group
  vpc_security_group_ids = [
    aws_security_group.ec2_sg.id,
    data.aws_security_group.border_control.id
  ]

  subnet_id = data.aws_subnets.public.ids[0]
  tags = { Name = "NLB-SSH-Instance" }
}

# --- ATTACHMENT ---
# Connect the EC2 instance to the NLB's target group. This is unchanged.
resource "aws_lb_target_group_attachment" "ssh_attachment" {
  target_group_arn = aws_lb_target_group.ssh_tg.arn
  target_id        = aws_instance.web_server.id
  port             = 22
}


# --- OUTPUTS ---
# Display the NLB's address after deployment. This is the address you will use for SSH.
output "nlb_dns_name" {
  description = "The DNS name of the Network Load Balancer for SSH."
  value       = aws_lb.ssh_nlb.dns_name
}

output "used_custom_ami_id" {
  description = "The custom AMI ID retrieved from the Service Catalog product and used for the instance."
  value       = aws_instance.web_server.ami
}