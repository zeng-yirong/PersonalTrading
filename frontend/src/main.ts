import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import { applyTheme, readTheme } from './theme'

// 在挂载前写入 data-theme，避免白天主题刷新时先闪一下深色。
applyTheme(readTheme())

createApp(App).mount('#app')
