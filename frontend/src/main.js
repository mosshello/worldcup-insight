import { createApp } from "vue";
import {
  Button,
  Select,
  Input,
  Textarea,
  Checkbox,
  CheckboxGroup,
  RadioGroup,
  Table,
  Pagination,
  Tag,
  Alert,
  Tabs,
  TabPanel,
  Drawer,
  Dialog,
  DatePicker,
} from "tdesign-vue-next";
import "tdesign-vue-next/es/style/index.css";
import "./styles.css";
import App from "./Workbench.vue";
const app = createApp(App);
for (const component of [
  Button,
  Select,
  Input,
  Textarea,
  Checkbox,
  CheckboxGroup,
  RadioGroup,
  Table,
  Pagination,
  Tag,
  Alert,
  Tabs,
  TabPanel,
  Drawer,
  Dialog,
  DatePicker,
])
  app.use(component);
app.mount("#app");
