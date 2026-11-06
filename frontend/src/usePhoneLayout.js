import { onBeforeUnmount, ref } from 'vue';

export function usePhoneLayout() {
  const media = window.matchMedia('(max-width: 760px)');
  const phone = ref(media.matches);
  const update = event => { phone.value = event.matches; };
  media.addEventListener('change', update);
  onBeforeUnmount(() => media.removeEventListener('change', update));
  return phone;
}
