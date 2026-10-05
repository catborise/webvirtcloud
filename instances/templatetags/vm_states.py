from django import template

from instances.states import allowed_actions, state_label

register = template.Library()


@register.simple_tag
def vm_state(state):
    """{% vm_state state as label_color %}: label and Bootstrap color."""
    return state_label(state)


@register.simple_tag(takes_context=True)
def vm_actions(context, instance):
    """{% vm_actions instance as actions %}: the actions this user may use now."""
    if not instance.info:
        return set()
    return allowed_actions(context["request"].user, instance, instance.info[0])
