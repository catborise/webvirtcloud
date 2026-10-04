import ipaddress
import re

from django import forms
from django.utils.translation import gettext_lazy as _


class AddInterface(forms.Form):
    name = forms.CharField(max_length=10, required=True)
    itype = forms.ChoiceField(
        required=True, choices=(("bridge", "bridge"), ("ethernet", "ethernet"))
    )
    start_mode = forms.ChoiceField(
        required=True,
        choices=(("none", "none"), ("onboot", "onboot"), ("hotplug", "hotplug")),
    )
    netdev = forms.CharField(max_length=15, required=True)
    ipv4_type = forms.ChoiceField(
        required=True,
        choices=(("dhcp", "dhcp"), ("static", "static"), ("none", "none")),
    )
    ipv4_addr = forms.CharField(max_length=18, required=False)
    ipv4_gw = forms.CharField(max_length=15, required=False)
    ipv6_type = forms.ChoiceField(
        required=True,
        choices=(("dhcp", "dhcp"), ("static", "static"), ("none", "none")),
    )
    ipv6_addr = forms.CharField(max_length=100, required=False)
    ipv6_gw = forms.CharField(max_length=100, required=False)
    stp = forms.ChoiceField(required=False, choices=(("on", "on"), ("off", "off")))
    delay = forms.IntegerField(required=False)

    def clean_ipv4_addr(self):
        ipv4_addr = self.cleaned_data["ipv4_addr"]
        have_symbol = re.match("^[0-9./]+$", ipv4_addr)
        if not have_symbol:
            raise forms.ValidationError(
                _("The IPv4 address must not contain any special characters")
            )
        elif len(ipv4_addr) > 20:
            raise forms.ValidationError(
                _("The IPv4 address must not exceed 20 characters")
            )
        return ipv4_addr

    def clean_ipv4_gw(self):
        ipv4_gw = self.cleaned_data["ipv4_gw"]
        have_symbol = re.match("^[0-9.]+$", ipv4_gw)
        if not have_symbol:
            raise forms.ValidationError(
                _("The IPv4 gateway must not contain any special characters")
            )
        elif len(ipv4_gw) > 20:
            raise forms.ValidationError(
                _("The IPv4 gateway must not exceed 20 characters")
            )
        return ipv4_gw

    def clean_ipv6_addr(self):
        ipv6_addr = self.cleaned_data["ipv6_addr"]
        if not ipv6_addr:
            return ipv6_addr
        try:
            # Always address/prefix, as the interface XML needs both
            return str(ipaddress.IPv6Interface(ipv6_addr))
        except ValueError:
            raise forms.ValidationError(_("The IPv6 address is not valid (e.g. 2001:db8::10/64)"))

    def clean_ipv6_gw(self):
        ipv6_gw = self.cleaned_data["ipv6_gw"]
        if not ipv6_gw:
            return ipv6_gw
        try:
            return str(ipaddress.IPv6Address(ipv6_gw))
        except ValueError:
            raise forms.ValidationError(_("The IPv6 gateway is not valid"))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("ipv6_type") == "static" and not cleaned.get("ipv6_addr") and "ipv6_addr" not in self.errors:
            self.add_error("ipv6_addr", _("A static IPv6 configuration needs an address"))
        return cleaned

    def clean_name(self):
        name = self.cleaned_data["name"]
        have_symbol = re.match("^[a-z0-9.]+$", name)
        if not have_symbol:
            raise forms.ValidationError(
                _("The interface must not contain any special characters")
            )
        elif len(name) > 10:
            raise forms.ValidationError(
                _("The interface must not exceed 10 characters")
            )
        return name

    def clean_netdev(self):
        netdev = self.cleaned_data["netdev"]
        have_symbol = re.match("^[a-z0-9.:]+$", netdev)
        if not have_symbol:
            raise forms.ValidationError(
                _("The interface must not contain any special characters")
            )
        elif len(netdev) > 10:
            raise forms.ValidationError(
                _("The interface must not exceed 10 characters")
            )
        return netdev
