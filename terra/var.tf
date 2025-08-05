variable "aws_region" {
  description = "The AWS region to deploy resources in."
  type        = string
  default     = "us-east-1"
}

variable "my_ip" {
  description = "Your local public IP address for the SSH allow list. E.g., '1.2.3.4/32'."
  type        = string
  sensitive   = true # To avoid showing your IP in logs
}

variable "vpc_id" {
  description = "ID of the VPC to deploy into. If empty, the default VPC will be used."
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "The EC2 instance type."
  type        = string
  default     = "t2.micro"
}

variable "key_name" {
  description = "A unique name for the EC2 key pair to be created in AWS."
  type        = string
  default     = "nlb-ssh-key"
}

variable "public_key_path" {
  description = "Path to your SSH public key file (e.g., ~/.ssh/id_rsa.pub)."
  type        = string
}

# --- NEW VARIABLES ---
variable "sc_product_name" {
  description = "The name of the provisioned Service Catalog product to check."
  type        = string
  default     = "ec2-spoke"
}

variable "sc_product_ami_output_key" {
  description = "The name of the output key from the Service Catalog product that holds the custom AMI ID."
  type        = string
  default     = "CustomAmiId" # <-- IMPORTANT: Change this to your actual output key name
}

variable "existing_sg_name" {
  description = "The name of the existing security group to attach."
  type        = string
  default     = "sgBborder-control-core"
}